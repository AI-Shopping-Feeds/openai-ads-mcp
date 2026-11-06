"""Campaigns: objective, budget, schedule and targeting; plus location lookup."""

from __future__ import annotations

from typing import Any

from .. import safety
from ..client import OpenAIAdsClient, path_id
from ..safety import MoneyChange
from ._common import (
    budget_changes,
    check_choice,
    clean,
    get_entity,
    guarded_write,
    to_unix,
    units_to_micros,
    with_money_view,
)

BIDDING_TYPES = ("impressions", "clicks", "conversions")
PLATFORMS = ("ios_app", "android_app", "web", "desktop_web", "ios_web", "android_web")


def build_budget(
    daily_budget: float | None, lifetime_budget: float | None
) -> dict[str, int] | None:
    if daily_budget is not None and lifetime_budget is not None:
        raise ValueError("Set daily_budget or lifetime_budget, not both.")
    if daily_budget is not None:
        return {"daily_spend_limit_micros": units_to_micros(daily_budget, "daily_budget") or 0}
    if lifetime_budget is not None:
        micros = units_to_micros(lifetime_budget, "lifetime_budget") or 0
        if micros < 1_000_000:
            raise ValueError("lifetime_budget must be at least 1 unit of the account currency.")
        return {"lifetime_spend_limit_micros": micros}
    return None


def build_targeting(
    countries: list[str] | None,
    location_ids: list[str] | None,
    platforms: list[str] | None,
    custom_audience_ids: list[str] | None,
    excluded_custom_audience_ids: list[str] | None,
) -> dict[str, Any] | None:
    targeting: dict[str, Any] = {}
    locations: dict[str, Any] = {}
    if countries:
        locations["countries"] = [c.strip().upper() for c in countries if c.strip()]
    if location_ids:
        locations["include"] = [{"id": str(i)} for i in location_ids]
    if locations:
        targeting["locations"] = locations
    if platforms:
        bad = [p for p in platforms if p not in PLATFORMS]
        if bad:
            raise ValueError(f"Unknown platform(s) {bad}; use {', '.join(PLATFORMS)}.")
        targeting["platforms"] = {"included": list(platforms)}
    if custom_audience_ids:
        targeting["custom_audiences"] = {"ids": list(custom_audience_ids)}
    if excluded_custom_audience_ids:
        targeting["excluded_custom_audiences"] = {"ids": list(excluded_custom_audience_ids)}
    return targeting or None


def register(mcp, client: OpenAIAdsClient) -> None:
    def campaign_money(budget: dict[str, Any] | None) -> list[MoneyChange]:
        return budget_changes("new_campaign", None, budget)

    @mcp.tool()
    def openai_ads_list_campaigns(
        name: str | None = None,
        include_serving_issues: bool = False,
        order: str = "desc",
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List campaigns in the ad account (all statuses, archived included).

        name filters by exact, case-insensitive name (may match several).
        include_serving_issues adds explicit delivery blockers to each campaign. Budgets
        are shown in micros and in the account currency (budget_in_currency)."""
        params = clean(
            {
                "name": name,
                "order": check_choice(order, ("asc", "desc"), "order"),
                "include": ["serving_issues"] if include_serving_issues else None,
            }
        )
        items, truncated, cursor = client.paginate("/campaigns", params, max_results=max_results)
        return {
            "data": [with_money_view(c) for c in items],
            "count": len(items),
            "truncated": truncated,
            "next_after": cursor,
        }

    @mcp.tool()
    def openai_ads_get_campaign(
        campaign_id: str, include_serving_issues: bool = True
    ) -> dict[str, Any]:
        """Get one campaign: status, objective (bidding_type), budget, schedule, targeting,
        conversion event and, by default, serving issues that block delivery."""
        include = ["serving_issues"] if include_serving_issues else None
        return with_money_view(get_entity(client, "campaign", campaign_id, include))

    @mcp.tool()
    def openai_ads_create_campaign(
        name: str,
        bidding_type: str,
        daily_budget: float | None = None,
        lifetime_budget: float | None = None,
        status: str = "paused",
        description: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        countries: list[str] | None = None,
        location_ids: list[str] | None = None,
        platforms: list[str] | None = None,
        custom_audience_ids: list[str] | None = None,
        excluded_custom_audience_ids: list[str] | None = None,
        conversion_event_setting_id: str | None = None,
        product_feed_id: str | None = None,
        query_string_template: str | None = None,
        extra: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Create a campaign. It starts PAUSED unless status='active'.

        bidding_type (the objective, fixed after creation): impressions, clicks or
        conversions. Budget: exactly one of daily_budget or lifetime_budget, in the
        account currency (e.g. 50 = 50 USD); Maximize Results ad groups need a daily
        budget. conversions campaigns need conversion_event_setting_id (one active
        standard event setting; see openai_ads_list_event_settings).

        Targeting: countries (ISO codes, e.g. ['US']), location_ids (regions/markets from
        openai_ads_search_locations), platforms (ios_app, android_app, web, desktop_web,
        ios_web, android_web), custom_audience_ids to include and
        excluded_custom_audience_ids to exclude. Omitting locations targets all available
        locations. start_time/end_time: Unix seconds or ISO 8601 with an offset.
        product_feed_id makes a product-feed campaign (open beta). query_string_template
        adds tracking parameters, e.g. 'utm_source=openai&utm_campaign={campaign_id}'.
        extra is merged into the request body for fields not listed here.

        A budget above OPENAI_ADS_MAX_DAILY_BUDGET / OPENAI_ADS_MAX_LIFETIME_BUDGET, or
        status='active', needs confirm='APPLY'. An Idempotency-Key is sent so retries
        can't create duplicates; reuse idempotency_key only to retry the same request."""
        bidding = check_choice(bidding_type, BIDDING_TYPES, "bidding_type")
        state = check_choice(status, ("active", "paused"), "status")
        budget = build_budget(daily_budget, lifetime_budget)
        if budget is None:
            raise ValueError("Set daily_budget or lifetime_budget.")
        if bidding == "conversions" and not conversion_event_setting_id:
            raise ValueError("conversions campaigns need conversion_event_setting_id.")
        body: dict[str, Any] = clean(
            {
                "name": name,
                "status": state,
                "bidding_type": bidding,
                "budget": budget,
                "description": description,
                "start_time": to_unix(start_time, "start_time"),
                "end_time": to_unix(end_time, "end_time"),
                "targeting": build_targeting(
                    countries,
                    location_ids,
                    platforms,
                    custom_audience_ids,
                    excluded_custom_audience_ids,
                ),
                "conversion_event_setting_ids": (
                    [conversion_event_setting_id] if conversion_event_setting_id else None
                ),
                "mode": "product_feed" if product_feed_id else None,
                "product_feed_id": product_feed_id,
                "landing_page_configuration": (
                    {"query_string_template": query_string_template}
                    if query_string_template
                    else None
                ),
            }
        )
        body.update(extra or {})
        reasons = []
        if state == "active":
            reasons.append("the campaign is created active, so it can start spending")
        decision = safety.assess(
            client.config, changes=campaign_money(budget), creating=True, extra_reasons=reasons
        )
        key = idempotency_key or client.new_idempotency_key("campaign")
        return guarded_write(
            client,
            decision,
            confirm,
            body,
            lambda: with_money_view(
                client.request(
                    "POST",
                    "/campaigns",
                    json_body=body,
                    op="create_campaign",
                    idempotency_key=key,
                )
            ),
        )

    @mcp.tool()
    def openai_ads_update_campaign(
        campaign_id: str,
        name: str | None = None,
        description: str | None = None,
        daily_budget: float | None = None,
        lifetime_budget: float | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        targeting: dict[str, Any] | None = None,
        query_string_template: str | None = None,
        fields: dict[str, Any] | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Update a campaign. Changes the live account. Send only what changes.

        daily_budget / lifetime_budget set the new amount in the account currency (not an
        increment). A lifetime budget can switch to daily, not back, and can't go below
        spend so far. targeting REPLACES the campaign's targeting object: read the campaign
        first and send the complete intended targeting (locations, platforms, audiences).
        fields is sent as-is for anything else, e.g. {"end_time": null} to clear a field.
        bidding_type and the conversion event can't change. Use openai_ads_set_status to
        pause or activate, and openai_ads_archive to archive.

        The current campaign is read first and kept in the audit log. A budget rise above
        OPENAI_ADS_MAX_INCREASE_PCT, a budget whose current value can't be compared (e.g.
        lifetime to daily), or a value over the caps needs confirm='APPLY'."""
        cid = path_id(campaign_id, "campaign_id")
        body: dict[str, Any] = clean(
            {
                "name": name,
                "description": description,
                "budget": build_budget(daily_budget, lifetime_budget),
                "start_time": to_unix(start_time, "start_time"),
                "end_time": to_unix(end_time, "end_time"),
                "targeting": targeting,
                "landing_page_configuration": (
                    {"query_string_template": query_string_template}
                    if query_string_template
                    else None
                ),
            }
        )
        body.update(fields or {})
        if not body:
            raise ValueError("Pass at least one field to change.")
        if "bidding_type" in body:
            raise ValueError("bidding_type can't change; create a new campaign instead.")
        current = client.snapshot(get_entity, client, "campaign", cid)
        reasons = []
        if body.get("status") == "active":
            reasons.append("status 'active' lets the campaign spend")
        decision = safety.assess(
            client.config,
            changes=budget_changes(cid, current, body.get("budget")),
            extra_reasons=reasons,
        )
        return guarded_write(
            client,
            decision,
            confirm,
            body,
            lambda: with_money_view(
                client.request(
                    "POST",
                    f"/campaigns/{cid}",
                    json_body=body,
                    op="update_campaign",
                    before=current,
                    idempotent=True,
                )
            ),
        )

    @mcp.tool()
    def openai_ads_search_locations(query: str, limit: int = 20) -> dict[str, Any]:
        """Find targetable locations (countries' regions and markets) by name, e.g.
        'California' or 'San Francisco'. Use the returned id in location_ids when creating
        or updating a campaign; name and type help pick the right one."""
        if not query.strip():
            raise ValueError("Pass a search query.")
        return client.request(
            "GET", "/geo_lookup/search", params={"q": query, "limit": max(1, min(limit, 100))}
        )
