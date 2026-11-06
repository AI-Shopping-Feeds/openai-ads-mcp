"""Thin HTTP client for the OpenAI Ads Advertiser API.

Every request goes through `OpenAIAdsClient.request`, the single choke point for
auth headers, rate limiting, retries, dry-run, the audit log and redaction. It
only talks to https://api.ads.openai.com/v1.
"""

from __future__ import annotations

import collections
import json
import random
import re
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from . import __version__
from ._logging import get_logger
from .audit import AuditLog
from .auth import ApiKeyAuth, redact
from .config import API_BASE, Config

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
MAX_BACKOFF = 120.0  # cap on honouring a very long Retry-After
# Responses larger than this are refused rather than held in memory.
MAX_RESPONSE_BYTES = 200 * 1024 * 1024
# Documented limits: 600 requests/min per endpoint and 1,200/min overall, per ad
# account and IP; bulk job creation 10 requests per 10 seconds. Stay below them.
OVERALL_LIMIT = (1000, 60.0)
BULK_CREATE_LIMIT = (10, 10.0)

_PATH_RE = re.compile(r"^/[A-Za-z0-9_\-./]+$")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-.:]{1,200}$")

logger = get_logger("client")


class OpenAIAdsAPIError(Exception):
    def __init__(self, status: int, body: Any, message: str = ""):
        self.status = status
        self.body = redact(body)
        hint = _HINTS.get(status, "")
        super().__init__(
            message or f"OpenAI Ads API error {status}: {self.body}{(' ' + hint) if hint else ''}"
        )


_HINTS = {
    401: "Check the Ads API key (Ads Manager -> Settings -> API keys); it may be revoked.",
    403: "The key can't do this, or the feature isn't enabled for the ad account.",
    404: "Check the ID; some features (pixels, bulk jobs, feeds) return 404 until enabled.",
    409: "Conflict: re-read the resource (stale revision or concurrent change) and retry.",
    413: "Too large: narrow the request (fewer entities, shorter date range).",
}


def path_id(value: Any, what: str = "id") -> str:
    """Validate an ID before it goes into a URL path (no slashes or dots tricks)."""
    text = str(value or "").strip()
    if not _ID_RE.match(text) or text in {".", ".."}:
        raise ValueError(f"{what} '{text}' is not a valid ID.")
    return text


def check_path(path: str) -> str:
    """A path relative to https://api.ads.openai.com/v1, e.g. '/campaigns'."""
    if (
        not path.startswith("/")
        or "://" in path
        or ".." in path
        or "//" in path
        or not _PATH_RE.match(path)
    ):
        raise ValueError(
            "path must be relative to https://api.ads.openai.com/v1 and contain only letters, "
            "digits, '_', '-', '.' and '/', e.g. '/campaigns' (pass query values in `query`)."
        )
    if path == "/v1" or path.startswith("/v1/"):
        raise ValueError("Leave out the /v1 prefix: path is relative to .../v1, e.g. '/campaigns'.")
    return path


def encode_params(
    params: dict[str, Any] | None,
) -> list[tuple[str, str | int | float | bool | None]]:
    """Encode query parameters the way the API documents them.

    Lists become repeated `key[]=value` pairs (e.g. `include[]=serving_issues`),
    booleans become true/false, dicts become JSON strings, None is dropped.
    """
    out: list[tuple[str, str | int | float | bool | None]] = []

    def one(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, dict | list):
            return json.dumps(value, separators=(",", ":"))
        return str(value)

    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, list | tuple):
            name = key if key.endswith("[]") else f"{key}[]"
            out.extend((name, one(v)) for v in value if v is not None)
        else:
            out.append((key, one(value)))
    return out


class RateLimiter:
    """Sliding-window limiter: at most `limit` calls per `window` seconds."""

    def __init__(self, limit: int, window: float, sleep: Callable[[float], None] | None = None):
        self.limit = limit
        self.window = window
        self._calls: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()
        self._sleep = sleep

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window:
                    self._calls.popleft()
                if len(self._calls) < self.limit:
                    self._calls.append(now)
                    return
                wait = self.window - (now - self._calls[0])
            (self._sleep or time.sleep)(max(wait, 0.01))


class OpenAIAdsClient:
    """Thin HTTP wrapper over the OpenAI Ads Advertiser API.

    Adds the bearer key (and OpenAI-Ad-Account for partner keys), stays under
    the documented rate limits, retries throttled requests with backoff
    (honouring Retry-After), paginates cursor lists, and sends every write
    through dry-run and the audit log. Writes are retried after a server error
    only when they carry an Idempotency-Key or are naturally idempotent, so a
    retry can't create a duplicate.
    """

    def __init__(self, config: Config, auth: ApiKeyAuth, audit: AuditLog):
        self.config = config
        self.auth = auth
        self.audit = audit
        self._http = httpx.Client(
            timeout=config.timeout,
            headers={"User-Agent": f"openai-ads-mcp/{__version__}"},
            follow_redirects=False,
        )
        self._overall = RateLimiter(*OVERALL_LIMIT)
        self._bulk = RateLimiter(*BULK_CREATE_LIMIT)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> OpenAIAdsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        return API_BASE

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        files: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        op: str | None = None,
        before: Any | None = None,
        read_only: bool = False,
        idempotency_key: str | None = None,
        idempotent: bool = False,
        audit_body: Any | None = None,
        ad_account_id: str | None = None,
    ) -> Any:
        """Perform one API request and return parsed JSON ({} for an empty body).

        path: relative to https://api.ads.openai.com/v1, starting with '/'.
        read_only: True for POSTs that change nothing (queries, previews,
            validate-only jobs): they bypass dry-run and the audit log.
        idempotency_key: sent as Idempotency-Key; makes the write safe to retry.
        idempotent: True for updates and state changes that are safe to repeat.
        audit_body: what to record in the audit log instead of the request body
            (e.g. with audience identifiers replaced by a count).
        files/form: send multipart/form-data instead of JSON.
        """
        method = method.upper()
        check_path(path)
        url = f"{self.base_url}{path}"
        is_write = method in WRITE_METHODS and not read_only
        recorded_body = audit_body if audit_body is not None else json_body
        if recorded_body is None and form is not None:
            recorded_body = {"form": form, "files": sorted((files or {}).keys())}
        account = ad_account_id or self.auth.ad_account_id
        operation = op or f"{method} {path}"

        if is_write and self.config.dry_run:
            audit_id = self.audit.record(
                op=operation,
                resource=path,
                method=method,
                url=url,
                ad_account_id=account,
                request_body=recorded_body,
                dry_run=True,
                before=before,
                idempotency_key=idempotency_key,
            )
            logger.info("dry-run %s %s (audit=%s)", method, path, audit_id)
            return {
                "_dry_run": True,
                "method": method,
                "url": url,
                "body": redact(recorded_body),
                "audit_id": audit_id,
            }

        retry_safe = (not is_write) or idempotent or bool(idempotency_key)
        last_exc: Exception | None = None
        attempt = 0
        while attempt < MAX_ATTEMPTS:
            self._overall.acquire()
            if method == "POST" and path == "/bulk_mutation_jobs":
                self._bulk.acquire()
            headers = {**self.auth.headers(ad_account_id), "Accept": "application/json"}
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            content: bytes | None = None
            if json_body is not None and files is None:
                headers["Content-Type"] = "application/json"
                content = json.dumps(json_body).encode("utf-8")
            try:
                status, payload, resp_headers = self._send(
                    method,
                    url,
                    params=encode_params(params),
                    content=content,
                    files=files,
                    data=form,
                    headers=headers,
                )
            except httpx.RequestError as e:
                last_exc = e
                logger.warning(
                    "transport error attempt=%d %s %s: %s",
                    attempt + 1,
                    method,
                    path,
                    redact(str(e)),
                )
                if not retry_safe:
                    break
                self._sleep_backoff(attempt)
                attempt += 1
                continue

            retryable = status in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS - 1
            # 429 means the request wasn't processed, so it is always safe to repeat.
            if retryable and (status == 429 or retry_safe):
                logger.warning(
                    "retryable status=%d attempt=%d %s %s", status, attempt + 1, method, path
                )
                self._sleep_backoff(attempt, resp_headers.get("Retry-After"))
                attempt += 1
                continue

            if not (200 <= status < 300):
                message = ""
                if is_write and status >= 500 and not retry_safe:
                    message = (
                        f"OpenAI Ads API error {status}: {redact(payload)}. The change may "
                        "have been saved before the error: read the resource before retrying."
                    )
                if is_write:
                    self.audit.record(
                        op=operation,
                        resource=path,
                        method=method,
                        url=url,
                        ad_account_id=account,
                        request_body=recorded_body,
                        response_status=status,
                        response_body=payload,
                        before=before,
                        error=f"HTTP {status}",
                        idempotency_key=idempotency_key,
                    )
                logger.error("api error %d %s %s: %s", status, method, path, redact(payload))
                raise OpenAIAdsAPIError(status, payload, message)

            if is_write:
                audit_id = self.audit.record(
                    op=operation,
                    resource=path,
                    method=method,
                    url=url,
                    ad_account_id=account,
                    request_body=recorded_body,
                    response_status=status,
                    response_body=payload,
                    before=before,
                    idempotency_key=idempotency_key,
                )
                logger.info("write %s %s -> %d (audit=%s)", method, path, status, audit_id)
                if isinstance(payload, dict):
                    payload = {**payload, "audit_id": audit_id}
                else:
                    payload = {"result": payload, "audit_id": audit_id}
            else:
                logger.debug("read  %s %s -> %d", method, path, status)
            return payload

        if is_write:
            self.audit.record(
                op=operation,
                resource=path,
                method=method,
                url=url,
                ad_account_id=account,
                request_body=recorded_body,
                before=before,
                error=f"transport error: {last_exc}",
                idempotency_key=idempotency_key,
            )
        detail = redact(str(last_exc)) if last_exc else "exhausted retries"
        if is_write and not retry_safe:
            detail += ". The change may have been saved: read the resource before retrying."
        raise OpenAIAdsAPIError(0, detail)

    def _send(
        self,
        method: str,
        url: str,
        *,
        params: list[tuple[str, str | int | float | bool | None]],
        content: bytes | None,
        files: dict[str, Any] | None,
        data: dict[str, str] | None,
        headers: dict[str, str],
    ) -> tuple[int, Any, httpx.Headers]:
        """Send one request and read at most MAX_RESPONSE_BYTES of the body."""
        req = self._http.build_request(
            method, url, params=params, content=content, files=files, data=data, headers=headers
        )
        resp = self._http.send(req, stream=True)
        try:
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                raise OpenAIAdsAPIError(
                    resp.status_code, f"response of {declared} bytes is over the size cap"
                )
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise OpenAIAdsAPIError(
                        resp.status_code,
                        f"response is over the {MAX_RESPONSE_BYTES // (1024 * 1024)} MB cap; "
                        "narrow the request",
                    )
                chunks.append(chunk)
        finally:
            resp.close()
        raw = b"".join(chunks)
        return resp.status_code, self._parse(resp.status_code, raw), resp.headers

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_results: int = 200,
        page_limit: int | None = None,
        max_page: int = 500,
    ) -> tuple[list[Any], bool, str | None]:
        """Follow `after=last_id` while `has_more`. Returns (items, truncated, next_cursor)."""
        params = dict(params or {})
        items: list[Any] = []
        page_size = page_limit or self.config.page_size
        while True:
            params["limit"] = max(1, min(page_size, max_page, max_results - len(items)))
            payload = self.request("GET", path, params=params)
            data = payload.get("data") if isinstance(payload, dict) else None
            page = data if isinstance(data, list) else []
            items.extend(page)
            has_more = bool(isinstance(payload, dict) and payload.get("has_more"))
            cursor = payload.get("last_id") if isinstance(payload, dict) else None
            if not has_more or not page or not cursor:
                return items, False, None
            if len(items) >= max_results:
                return items[:max_results], True, cursor
            params["after"] = cursor

    def snapshot(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any | None:
        """Run a read for a `before` snapshot; returns None instead of raising."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.warning("before-snapshot failed: %s", redact(str(e)))
            return None

    @staticmethod
    def new_idempotency_key(prefix: str = "openai-ads-mcp") -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    @staticmethod
    def _parse(status: int, raw: bytes) -> Any:
        if status == 204 or not raw.strip():
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"_raw": raw[:2000].decode("utf-8", "replace")}

    @staticmethod
    def _sleep_backoff(attempt: int, retry_after: str | None = None) -> None:
        wait = 0.0
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                wait = 0.0
        base = min(max(wait, min(2**attempt, 30)), MAX_BACKOFF)
        time.sleep(base + random.uniform(0, 0.5))
