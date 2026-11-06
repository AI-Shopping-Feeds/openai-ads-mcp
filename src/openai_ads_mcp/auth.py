"""Ads API key handling and secret redaction.

The OpenAI Ads Advertiser API authenticates with a bearer Ads API key created in
Ads Manager (Settings -> API keys). Each key is scoped to one ad account. The
key comes from OPENAI_ADS_API_KEY or from the 0600 file `openai-ads-mcp auth`
writes, and is never logged: use `redact()` on anything that might contain it.

Some endpoints return a credential exactly once (Conversions API keys, SFTP
passwords, lead-sync signing secrets, new API keys). `stash_secrets()` moves those
values into an owner-only file and leaves a pointer in the tool result, so they
don't end up in the chat transcript or the audit log.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Config, created_secrets_dir, write_private_json

# Field names whose values are always secret.
SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "password",
        "signing_secret",
        "access_token",
        "refresh_token",
        "client_secret",
        "secret",
        "upload_headers",
    }
)
# Bearer headers and OpenAI-style secret keys that might appear in free text.
_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
)
_extra_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret value (e.g. the API key) so redact() hides it anywhere."""
    if value and len(value) >= 8:
        _extra_secrets.add(value)


def redact(value: Any) -> Any:
    """Return a copy of value with keys, passwords and tokens replaced by '***'."""
    if isinstance(value, str):
        out = value
        for secret in _extra_secrets:
            out = out.replace(secret, "***")
        for pattern in _PATTERNS:
            out = pattern.sub("***", out)
        return out
    if isinstance(value, dict):
        return {
            k: ("***" if str(k).lower() in SECRET_KEYS and v else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class ApiKeyAuth:
    """Holds the Ads API key and builds the auth headers for each request."""

    def __init__(self, api_key: str, ad_account_id: str | None = None):
        if not api_key:
            raise RuntimeError("No Ads API key configured. Run `openai-ads-mcp auth` first.")
        self._api_key = api_key
        self.ad_account_id = ad_account_id
        register_secret(api_key)

    @property
    def account_label(self) -> str:
        return f"ads-api-key:...{self._api_key[-4:]}"

    def headers(self, ad_account_id: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        account = ad_account_id or self.ad_account_id
        if account:
            # Only partner keys that reach several accounts need this header.
            headers["OpenAI-Ad-Account"] = account
        return headers


def load_auth(config: Config) -> ApiKeyAuth:
    if not config.api_key:
        raise RuntimeError("No Ads API key configured. Run `openai-ads-mcp auth` first.")
    return ApiKeyAuth(config.api_key, config.ad_account_id)


def save_key_file(path: Path, api_key: str, ad_account_id: str | None = None) -> None:
    """Write the key file with owner-only (0600) permissions."""
    data: dict[str, Any] = {
        "api_key": api_key,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if ad_account_id:
        data["ad_account_id"] = ad_account_id
    write_private_json(path, data)


def stash_secrets(
    payload: Any, *, kind: str, label: str | None = None, directory: Path | None = None
) -> Any:
    """Move secret fields of an API response into a 0600 file.

    Returns the payload with each secret replaced by a note, plus
    `secret_saved_to` with the file path. Payloads without secrets are returned
    unchanged.
    """
    if not isinstance(payload, dict):
        return payload
    found = {k: v for k, v in payload.items() if str(k).lower() in SECRET_KEYS and v}
    if not found:
        return payload
    folder = directory or created_secrets_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_label = re.sub(r"[^A-Za-z0-9_\-]+", "-", label or "")[:40].strip("-")
    name = f"{kind}-{safe_label + '-' if safe_label else ''}{stamp}-{uuid.uuid4().hex[:6]}.json"
    path = folder / name
    write_private_json(
        path,
        {
            "kind": kind,
            "label": label,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            **found,
        },
    )
    for value in found.values():
        if isinstance(value, str):
            register_secret(value)
    out = {
        k: (f"saved to {path} (owner-only file); not shown here" if k in found else v)
        for k, v in payload.items()
    }
    out["secret_saved_to"] = str(path)
    return out
