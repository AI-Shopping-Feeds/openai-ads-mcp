"""Lead sync: deliver leads to a webhook endpoint the advertiser controls.

These endpoints are in OpenAI's OpenAPI spec but have no guide yet, so treat
them as early access: availability depends on the account.
"""

from __future__ import annotations

from typing import Any

from .. import safety
from ..auth import stash_secrets
from ..client import OpenAIAdsClient, path_id
from ._common import clean, guarded_write


def register(mcp, client: OpenAIAdsClient) -> None:
    def account_id(value: str | None) -> str:
        if value:
            return path_id(value, "ad_account_id")
        if client.auth.ad_account_id:
            return client.auth.ad_account_id
        account = client.request("GET", "/ad_account")
        aid = account.get("id") if isinstance(account, dict) else None
        if not aid:
            raise RuntimeError("Couldn't read the ad account ID; pass ad_account_id.")
        return str(aid)

    @mcp.tool()
    def openai_ads_list_lead_sync_subscriptions(ad_account_id: str | None = None) -> dict[str, Any]:
        """List the lead-sync webhook subscription for the ad account (early access).
        ad_account_id defaults to the key's account."""
        return client.request(
            "GET", "/lead_sync_subscriptions", params={"ad_account_id": account_id(ad_account_id)}
        )

    @mcp.tool()
    def openai_ads_get_lead_sync_subscription(subscription_id: str) -> dict[str, Any]:
        """Get one lead-sync webhook subscription and its status (early access)."""
        sid = path_id(subscription_id, "subscription_id")
        return client.request("GET", f"/lead_sync_subscriptions/{sid}")

    @mcp.tool()
    def openai_ads_create_lead_sync_subscription(
        destination_url: str,
        ad_account_id: str | None = None,
        idempotency_key: str | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Start delivering the account's leads to destination_url, an https webhook the
        advertiser controls (early access). Leads are personal data, so check the URL with
        the user; this needs confirm='APPLY'. The returned signing secret is saved to an
        owner-only file under ~/.openai-ads-mcp/secrets/created/ and only its path is
        shown."""
        if not destination_url.startswith("https://"):
            raise ValueError("destination_url must be an https:// URL.")
        body = {"ad_account_id": account_id(ad_account_id), "destination_url": destination_url}
        decision = safety.assess(
            client.config,
            extra_reasons=[f"leads (personal data) will be sent to {destination_url}"],
        )
        key = idempotency_key or client.new_idempotency_key("lead-sync")

        def send() -> Any:
            out = client.request(
                "POST",
                "/lead_sync_subscriptions",
                json_body=body,
                op="create_lead_sync_subscription",
                idempotency_key=key,
            )
            return stash_secrets(
                out,
                kind="lead-sync-signing-secret",
                label=body["ad_account_id"],
                directory=client.config.secrets_dir / "created",
            )

        return guarded_write(client, decision, confirm, body, send)

    @mcp.tool()
    def openai_ads_delete_lead_sync_subscription(
        subscription_id: str, confirm: str = ""
    ) -> dict[str, Any]:
        """Stop and delete a lead-sync webhook subscription (early access). Needs
        confirm='DELETE' after the user agrees."""
        sid = path_id(subscription_id, "subscription_id")
        decision = safety.assess(client.config, destructive=safety.DELETE)
        before = client.snapshot(client.request, "GET", f"/lead_sync_subscriptions/{sid}")
        return guarded_write(
            client,
            decision,
            confirm,
            clean({"method": "DELETE", "path": f"/lead_sync_subscriptions/{sid}"}),
            lambda: client.request(
                "DELETE",
                f"/lead_sync_subscriptions/{sid}",
                op="delete_lead_sync_subscription",
                before=before,
                idempotent=True,
            ),
        )
