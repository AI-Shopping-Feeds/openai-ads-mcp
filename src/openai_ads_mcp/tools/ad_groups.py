"""Ad groups: bidding, context hints, audience bid multipliers and product sets."""

from __future__ import annotations

from typing import Any

from .. import safety
from ..client import OpenAIAdsClient, path_id
from ._common import (
    bid_changes,
    check_choice,
    clean,
    get_entity,
    guarded_write,
    multipliers_to_api,
    units_to_micros,
    with_money_view,
)

STRATEGIES = ("fixed_bid", "maximize_clicks", "maximize_conversions")
BILLING_EVENTS = ("impression", "click")


def list_ad_groups(
    client: OpenAIAdsClient,
    campaign_id: str | None,
    *,
    name: str | None = None,
    include_serving_issues: bool = False,
    max_results: int = 200,
) -> tuple[list[Any], bool]:
    """Ad groups of one campaign, or of every campaign when campaign_id is None."""
    base = clean({"name": name, "include": ["serving_issues"] if include_serving_issues else None})
    if campaign_id:
        items, truncated, _ = client.paginate(
            "/ad_groups",
            {**base, "campaign_id": path_id(campaign_id, "campaign_id")},
            max_results=max_results,
        )
        return items, truncated
    campaigns, truncated, _ = client.paginate("/campaigns", max_results=1000)
    out: list[Any] = []
    for c in campaigns:
        if len(out) >= max_results:
            return out[:max_results], True
        cid = c.get("id") if isinstance(c, dict) else None
        if not cid or c.get("status") == "archived":
            continue
        items, more, _ = client.paginate(
            "/ad_groups", {**base, "campaign_id": cid}, max_results=max_results - len(out)
        )
        out.extend({**g, "campaign_id": g.get("campaign_id", cid)} for g in items)
        truncated = truncated or more
    return out, truncated


def register(mcp, client: OpenAIAdsClient) -> None:
    @mcp.tool()
    def openai_ads_list_ad_groups(
        campaign_id: str | None = None,
        name: str | None = None,
        include_serving_issues: bool = False,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List ad groups of a campaign. Without campaign_id, lists the ad groups of every
        non-archived campaign (one request per campaign). name filters by exact,
        case-insensitive name. Bids are shown in micros and in the account currency."""
        items, truncated = list_ad_groups(
            client,
            campaign_id,
            name=name,
            include_serving_issues=include_serving_issues,
            max_results=max_results,
        )
        return {
            "data": [with_money_view(g) for g in items],
            "count": len(items),
            "truncated": truncated,
        }

    @mcp.tool()
    def openai_ads_get_ad_group(
        ad_group_id: str, include_serving_issues: bool = True, include_bid_warning: bool = False
    ) -> dict[str, Any]:
        """Get one ad group: status, bidding_config, context hints, audience multipliers,
        product set and (by default) serving issues. include_bid_warning adds bid_too_low
        where OpenAI exposes it; it's guidance, not a block, and isn't raised for inactive
        ad groups or within 24 hours of an edit."""
        include = []
        if include_serving_issues:
            include.append("serving_issues")
        if include_bid_warning:
            include.append("bid_too_low")
        return with_money_view(get_entity(client, "ad_group", ad_group_id, include or None))

    @mcp.tool()
    def openai_ads_create_ad_group(
        campaign_id: str,
        name: str,
        billing_event_type: str,
        strategy: str = "fixed_bid",
        max_bid: float | None = None,
        context_hints: list[str] | None = None,
        description: str | None = None,
        status: str = "paused",
        audience_bid_multipliers: list[dict[str, Any]] | None = None,
        product_set: dict[str, Any] | None = None,
        query_string_template: str | None = None,
        extra: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Create an ad group in a campaign. It starts PAUSED unless status='active'.

        Bidding must match the campaign objective:
        impressions -> billing_event_type='impression', strategy='fixed_bid';
        clicks -> 'click' with 'fixed_bid' or 'maximize_clicks';
        conversions -> 'click' with 'fixed_bid' or 'maximize_conversions'.
        max_bid is in the account currency and is required for fixed_bid, omitted for
        Maximize Results (which needs a daily campaign budget). It is per event: for
        impressions a 60 CPM is max_bid=0.06; for a conversions campaign it is the CPA bid.

        context_hints: short descriptions of when the ad is useful (they guide placement,
        they aren't keywords). audience_bid_multipliers: [{"custom_audience_id": ...,
        "multiplier": 1.5}] (0.1-10, fixed_bid only; audiences from
        openai_ads_list_audiences with intended_use='bid_multiplier'). product_set:
        {"product_feed_id": ..., "filters": [{"field": "brand", "operator": "in",
        "values": ["Acme"]}]} for product-feed campaigns. extra is merged into the body.

        A max_bid above OPENAI_ADS_MAX_BID, or status='active', needs confirm='APPLY'."""
        billing = check_choice(billing_event_type, BILLING_EVENTS, "billing_event_type")
        strat = check_choice(strategy, STRATEGIES, "strategy")
        state = check_choice(status, ("active", "paused"), "status")
        if strat == "fixed_bid" and max_bid is None:
            raise ValueError("fixed_bid needs max_bid (in the account currency).")
        if strat != "fixed_bid" and max_bid is not None:
            raise ValueError(f"{strat} sets bids automatically; leave max_bid out.")
        if strat != "fixed_bid" and audience_bid_multipliers:
            raise ValueError("Audience bid multipliers work only with fixed_bid.")
        bidding = clean(
            {
                "billing_event_type": billing,
                "strategy": strat,
                "max_bid_micros": units_to_micros(max_bid, "max_bid"),
                "custom_audience_bid_multipliers": multipliers_to_api(audience_bid_multipliers),
            }
        )
        body: dict[str, Any] = clean(
            {
                "campaign_id": path_id(campaign_id, "campaign_id"),
                "name": name,
                "status": state,
                "bidding_config": bidding,
                "context_hints": context_hints,
                "description": description,
                "product_set": product_set,
                "landing_page_configuration": (
                    {"query_string_template": query_string_template}
                    if query_string_template
                    else None
                ),
            }
        )
        body.update(extra or {})
        changes, _ = bid_changes("new_ad_group", None, bidding)
        reasons = []
        if state == "active":
            reasons.append("the ad group is created active, so it can start spending")
        decision = safety.assess(
            client.config, changes=changes, creating=True, extra_reasons=reasons
        )
        key = idempotency_key or client.new_idempotency_key("ad-group")
        return guarded_write(
            client,
            decision,
            confirm,
            body,
            lambda: with_money_view(
                client.request(
                    "POST", "/ad_groups", json_body=body, op="create_ad_group", idempotency_key=key
                )
            ),
        )

    @mcp.tool()
    def openai_ads_update_ad_group(
        ad_group_id: str,
        name: str | None = None,
        description: str | None = None,
        context_hints: list[str] | None = None,
        max_bid: float | None = None,
        strategy: str | None = None,
        billing_event_type: str | None = None,
        audience_bid_multipliers: list[dict[str, Any]] | None = None,
        product_set: dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Update an ad group. Changes the live account. Send only what changes.

        context_hints REPLACES the list (include every hint to keep). Bidding changes
        (max_bid in the account currency, strategy, billing_event_type,
        audience_bid_multipliers) are merged into the current bidding_config, because the
        API needs the full object. Switching to maximize_clicks/maximize_conversions drops
        max_bid and the multipliers; switching back to fixed_bid needs max_bid.
        product_set replaces the product filters. fields is sent as-is for anything else.
        Use openai_ads_set_status to pause or activate.

        The current ad group is read first and kept in the audit log. A bid or multiplier
        rise above OPENAI_ADS_MAX_INCREASE_PCT, an unreadable current bid, a bid over
        OPENAI_ADS_MAX_BID, or a switch to automated bidding needs confirm='APPLY'."""
        gid = path_id(ad_group_id, "ad_group_id")
        current = client.snapshot(get_entity, client, "ad_group", gid)
        body: dict[str, Any] = clean(
            {
                "name": name,
                "description": description,
                "context_hints": context_hints,
                "product_set": product_set,
            }
        )
        bidding_requested = any(
            v is not None for v in (max_bid, strategy, billing_event_type, audience_bid_multipliers)
        )
        if bidding_requested:
            base = dict((current or {}).get("bidding_config") or {})
            if not base and (strategy is None or billing_event_type is None):
                raise ValueError(
                    "Couldn't read the current bidding_config; pass strategy and "
                    "billing_event_type along with the bid change."
                )
            config = dict(base)
            if billing_event_type is not None:
                config["billing_event_type"] = check_choice(
                    billing_event_type, BILLING_EVENTS, "billing_event_type"
                )
            if strategy is not None:
                config["strategy"] = check_choice(strategy, STRATEGIES, "strategy")
            config.setdefault("strategy", "fixed_bid")
            if max_bid is not None:
                config["max_bid_micros"] = units_to_micros(max_bid, "max_bid")
            if audience_bid_multipliers is not None:
                config["custom_audience_bid_multipliers"] = multipliers_to_api(
                    audience_bid_multipliers
                )
            if config["strategy"] != "fixed_bid":
                if max_bid is not None:
                    raise ValueError(f"{config['strategy']} sets bids automatically; omit max_bid.")
                config.pop("max_bid_micros", None)
                config["custom_audience_bid_multipliers"] = []
            elif not config.get("max_bid_micros"):
                raise ValueError("fixed_bid needs max_bid (in the account currency).")
            body["bidding_config"] = config
        body.update(fields or {})
        if not body:
            raise ValueError("Pass at least one field to change.")
        changes, reasons = bid_changes(gid, current, body.get("bidding_config"))
        if body.get("status") == "active":
            reasons.append("status 'active' lets the ad group spend")
        decision = safety.assess(client.config, changes=changes, extra_reasons=reasons)
        return guarded_write(
            client,
            decision,
            confirm,
            body,
            lambda: with_money_view(
                client.request(
                    "POST",
                    f"/ad_groups/{gid}",
                    json_body=body,
                    op="update_ad_group",
                    before=current,
                    idempotent=True,
                )
            ),
        )
