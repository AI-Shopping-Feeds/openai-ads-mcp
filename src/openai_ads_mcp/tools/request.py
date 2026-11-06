"""Escape hatch: call any OpenAI Ads API endpoint on https://api.ads.openai.com/v1.

Paths are checked (relative, no '..', no other hosts), query values go in `query`
(lists are sent as key[]=value), and any credential in a response is moved to an
owner-only file instead of being returned.
"""

from __future__ import annotations

import re
from typing import Any

from .. import safety
from ..auth import stash_secrets
from ..client import OpenAIAdsClient, check_path
from ..config import check_account_id

# POST endpoints that only read: product queries, conversion insights, ad previews.
_READ_POST = re.compile(r"^(/feeds/[^/]+/products/query|/conversions/insights|/ads/[^/]+/preview)$")


def _classify(method: str, path: str) -> tuple[str, bool]:
    method = method.upper()
    if method not in {"GET", "POST", "PATCH", "DELETE"}:
        raise ValueError("method must be GET, POST, PATCH or DELETE.")
    check_path(path)
    is_read = method == "GET" or (method == "POST" and bool(_READ_POST.match(path)))
    return method, is_read


def register(mcp, client: OpenAIAdsClient) -> None:
    def stash(out: Any, path: str) -> Any:
        return stash_secrets(
            out, kind="api-response", label=path, directory=client.config.secrets_dir / "created"
        )

    @mcp.tool()
    def openai_ads_read_request(
        method: str,
        path: str,
        body: Any | None = None,
        query: dict[str, Any] | None = None,
        ad_account_id: str | None = None,
    ) -> Any:
        """Read from any OpenAI Ads API endpoint the other tools don't cover.

        Only GET, and the read-only POSTs (/feeds/{id}/products/query,
        /conversions/insights, /ads/{id}/preview), are allowed; use openai_ads_request
        for anything else. path is relative to https://api.ads.openai.com/v1, e.g.
        '/campaigns/cmpn_123'. query values that are lists are sent as key[]=value.
        ad_account_id sends OpenAI-Ad-Account (partner keys only)."""
        method, is_read = _classify(method, path)
        if not is_read:
            raise ValueError(f"{method} {path} may change the account; use openai_ads_request.")
        out = client.request(
            method,
            path,
            params=query,
            json_body=body,
            read_only=True,
            ad_account_id=check_account_id(ad_account_id, "ad_account_id"),
        )
        return stash(out, path)

    @mcp.tool()
    def openai_ads_request(
        method: str,
        path: str,
        body: Any | None = None,
        query: dict[str, Any] | None = None,
        ad_account_id: str | None = None,
        idempotency_key: str | None = None,
        confirm: str = "",
    ) -> Any:
        """Call any OpenAI Ads API endpoint the other tools don't cover (GET, POST, PATCH or
        DELETE). path is relative to https://api.ads.openai.com/v1, e.g. '/campaigns'.

        GET and the read-only POSTs are reads. Every other call is a write: it is audited,
        follows dry-run, and needs confirm='APPLY' because this tool can't check what it
        changes or spends. idempotency_key is sent as Idempotency-Key (use it for creates).
        Credentials in a response (api_key, password, signing_secret) are saved to an
        owner-only file and only the path is returned."""
        method, is_read = _classify(method, path)
        if not is_read:
            decision = safety.Decision(
                required=safety.APPLY,
                reasons=["raw write through openai_ads_request; its effect isn't checked"],
            )
            refused = safety.refusal(
                client.config,
                decision,
                confirm,
                preview={"method": method, "path": path, "query": query, "body": body},
            )
            if refused:
                return refused
        out = client.request(
            method,
            path,
            params=query,
            json_body=body,
            read_only=is_read,
            op=f"request {method} {path}",
            idempotency_key=idempotency_key,
            ad_account_id=check_account_id(ad_account_id, "ad_account_id"),
        )
        return stash(out, path)
