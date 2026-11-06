"""The ad account: details, branding, account-wide pause/activate, change history."""

from __future__ import annotations

from typing import Any

from .. import safety
from ..client import OpenAIAdsClient, path_id
from ._common import clean, guarded_write, to_unix


def register(mcp, client: OpenAIAdsClient) -> None:
    def get_account() -> dict[str, Any]:
        out = client.request("GET", "/ad_account")
        return out if isinstance(out, dict) else {}

    @mcp.tool()
    def openai_ads_get_ad_account() -> dict[str, Any]:
        """Get the ad account this API key belongs to: ID, legal/account/brand names, URL,
        status, timezone, currency, brand review and account-integrity review.

        Check currency and timezone before creating campaigns: money fields are micros of
        this currency, dates are in this timezone. An 'active' account can still have an
        outstanding review; a missing review object is not an approval."""
        return get_account()

    @mcp.tool()
    def openai_ads_list_ad_accounts(max_results: int = 100) -> dict[str, Any]:
        """List the ad accounts this API key can reach. A standard key reaches one account;
        partner keys may reach several (set OPENAI_ADS_AD_ACCOUNT_ID to pick one)."""
        items, truncated, cursor = client.paginate("/ad_accounts", max_results=max_results)
        return {"data": items, "count": len(items), "truncated": truncated, "next_after": cursor}

    @mcp.tool()
    def openai_ads_update_account_brand(
        brand_name: str | None = None,
        account_name: str | None = None,
        legal_name: str | None = None,
        url: str | None = None,
        favicon_file_id: str | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Update the account's names, URL or advertiser icon. Changes the live account.

        brand_name is the public advertiser name shown in ads; account_name is the
        internal name in Ads Manager; legal_name is the legal business name. Names are
        2-1000 characters without '<' or '>'. favicon_file_id comes from
        openai_ads_upload_image with purpose='account_favicon'.

        Changing brand_name, legal_name or url starts a brand review and pauses delivery
        across the account until it passes, so it needs confirm='APPLY'. Not every
        account can edit branding through the API (403 means ask OpenAI to enable it)."""
        body = clean(
            {
                "brand_name": brand_name,
                "account_name": account_name,
                "legal_name": legal_name,
                "url": url,
                "favicon_file_id": favicon_file_id,
            }
        )
        if not body:
            raise ValueError("Pass at least one field to change.")
        before = client.snapshot(get_account)
        reasons = []
        if {"brand_name", "legal_name", "url"} & set(body):
            reasons.append(
                "changing the brand name, legal name or URL starts a brand review and pauses "
                "delivery until it passes"
            )
        decision = safety.assess(client.config, extra_reasons=reasons)
        return guarded_write(
            client,
            decision,
            confirm,
            body,
            lambda: client.request(
                "POST",
                "/ad_account/brand",
                json_body=body,
                op="update_account_brand",
                before=before,
                idempotent=True,
            ),
        )

    @mcp.tool()
    def openai_ads_pause_account() -> dict[str, Any]:
        """Pause the whole ad account: stops delivery across every campaign. Use a
        campaign, ad group or ad pause for anything narrower. Child statuses are not
        rewritten; activating the account later lets eligible campaigns deliver again."""
        before = client.snapshot(get_account)
        return client.request(
            "POST", "/ad_account/pause", op="pause_account", before=before, idempotent=True
        )

    @mcp.tool()
    def openai_ads_activate_account(confirm: str = "") -> dict[str, Any]:
        """Activate the ad account so its eligible campaigns can deliver (and spend) again.

        Affects every campaign in the account, so it needs confirm='APPLY'. Activation
        doesn't approve reviews, raise budgets or override exhausted spend limits."""
        before = client.snapshot(get_account)
        decision = safety.assess(
            client.config,
            extra_reasons=["activating the account resumes delivery for all active campaigns"],
        )
        return guarded_write(
            client,
            decision,
            confirm,
            {"method": "POST", "path": "/ad_account/activate"},
            lambda: client.request(
                "POST",
                "/ad_account/activate",
                op="activate_account",
                before=before,
                idempotent=True,
            ),
        )

    @mcp.tool()
    def openai_ads_list_account_changes(
        campaign_id: str | None = None,
        ad_group_id: str | None = None,
        ad_id: str | None = None,
        actor_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        order: str = "desc",
        max_results: int = 50,
    ) -> dict[str, Any]:
        """List OpenAI's record of changes in the ad account (its change log), newest first.

        Filters combine with AND: campaign_id includes its ad groups and ads; actor_id is
        who made the change. start_time/end_time are Unix seconds or ISO 8601 with an
        offset. This is OpenAI's history; this server's own log is openai_ads_audit_tail."""
        if order not in {"asc", "desc"}:
            raise ValueError("order must be asc or desc.")
        params = clean(
            {
                "campaign_id": path_id(campaign_id, "campaign_id") if campaign_id else None,
                "ad_group_id": path_id(ad_group_id, "ad_group_id") if ad_group_id else None,
                "ad_id": path_id(ad_id, "ad_id") if ad_id else None,
                "actor_id": actor_id,
                "start_time": to_unix(start_time, "start_time"),
                "end_time": to_unix(end_time, "end_time"),
                "order": order,
            }
        )
        items, truncated, cursor = client.paginate(
            "/audit_logs", params, max_results=max_results, max_page=100
        )
        return {"data": items, "count": len(items), "truncated": truncated, "next_after": cursor}
