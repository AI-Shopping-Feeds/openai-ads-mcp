"""Insights: delivery metrics (impressions, clicks, spend, CTR, CPC, CPM), conversions
and Hotel reporting.

Insights are synchronous and paginated. Rows are returned to the caller and kept
in memory only: never written to disk or the audit log. Spend can lag
impressions and clicks by hours; conversions need about a day.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.mcpserver import Context

from ..client import OpenAIAdsClient, path_id
from ._common import check_choice, check_date, clean, date_range
from ._progress import report, run_blocking

SCOPES = {
    "ad_account": "/ad_account/insights",
    "campaign": "/campaigns/{id}/insights",
    "ad_group": "/ad_groups/{id}/insights",
    "ad": "/ads/{id}/insights",
}
LEVELS = ("ad_account", "campaign", "ad_group", "ad")
GRANULARITY = ("hourly", "daily", "monthly", "none")
SEGMENTS = ("product", "country", "device", "platform")

GUIDE = """\
Fields use canonical names: '{level}.{metric}' with metrics impressions, clicks, spend,
ctr, cpc, cpm (and conversions where enabled), e.g. 'campaign.spend'; IDs and metadata
such as 'campaign.id', 'campaign.name', 'ad.title', 'ad.review_status';
'metadata.readable_time' for the bucket date. Rows come back with flat keys
(campaign_id, spend, readable_time). filters: [{"field": "campaign.id", "operator":
"IN", "value": ["cmpn_123"]}] or {"field": "ad.clicks", "operator": "GREATER_THAN",
"value": 10}. sort: [{"field": "ad.clicks", "direction": "desc"}]. segments: one of
product, country, device, platform (enabled accounts; segmented requests support
none/daily/monthly). Spend is in the account currency. History: about 365 days (30 for
hourly and product reports)."""


def _insights_params(
    *,
    aggregation_level: str | None,
    start_date: str | None,
    end_date: str | None,
    time_range: dict[str, Any] | None,
    time_granularity: str | None,
    fields: list[str] | None,
    filters: list[dict[str, Any]] | None,
    sort: list[dict[str, Any]] | None,
    segments: list[str] | None,
    override_segment_group_order: list[str] | None,
    includes: list[str] | None,
    attribution_window_days: int | None,
    view_through_attribution_window_days: int | None,
    attribution_time_basis: str | None,
) -> dict[str, Any]:
    if time_range is not None:
        ranges = [time_range]
    elif start_date and end_date:
        ranges = [date_range(start_date, end_date)]
    else:
        raise ValueError("Pass start_date and end_date (YYYY-MM-DD), or time_range.")
    if segments:
        bad = [s for s in segments if s not in SEGMENTS]
        if bad:
            raise ValueError(f"Unknown segment(s) {bad}; use one of {', '.join(SEGMENTS)}.")
    return clean(
        {
            "aggregation_level": check_choice(aggregation_level, LEVELS, "aggregation_level"),
            "time_granularity": check_choice(time_granularity, GRANULARITY, "time_granularity"),
            "time_ranges": ranges,
            "fields": fields or None,
            "filters": filters or None,
            "sort": sort or None,
            "segments": segments or None,
            "override_segment_group_order": override_segment_group_order or None,
            "includes": includes or None,
            "attribution_window_days": attribution_window_days,
            "view_through_attribution_window_days": view_through_attribution_window_days,
            "attribution_time_basis": check_choice(
                attribution_time_basis,
                ("ad_event_time", "conversion_time"),
                "attribution_time_basis",
            ),
        }
    )


def _scope_path(scope: str, entity_id: str | None) -> str:
    kind = check_choice(scope, LEVELS, "scope") or "ad_account"
    if kind == "ad_account":
        return SCOPES[kind]
    if not entity_id:
        raise ValueError(f"scope '{kind}' needs entity_id (the {kind} ID).")
    return SCOPES[kind].format(id=path_id(entity_id, "entity_id"))


def register(mcp, client: OpenAIAdsClient) -> None:
    def fetch_page(path: str, params: dict[str, Any]) -> dict[str, Any]:
        out = client.request("GET", path, params=params)
        return out if isinstance(out, dict) else {"data": out}

    @mcp.tool(
        description=(
            "Get one page of delivery insights (impressions, clicks, spend, CTR, CPC, CPM).\n\n"
            "scope sets which entity the report covers: ad_account (default), campaign, "
            "ad_group or ad (with entity_id). aggregation_level picks the row entity inside "
            "it (e.g. scope ad_account + aggregation_level campaign = one row per campaign). "
            "Dates are YYYY-MM-DD, inclusive, in the account timezone (or pass a raw "
            "time_range object). time_granularity: none (one total), hourly, daily (API "
            "default) or monthly. limit 1-2000; pass the returned last_id as after for the "
            "next page, or use openai_ads_run_insights to fetch every page.\n\n" + GUIDE
        )
    )
    def openai_ads_get_insights(
        start_date: str | None = None,
        end_date: str | None = None,
        scope: str = "ad_account",
        entity_id: str | None = None,
        aggregation_level: str | None = None,
        fields: list[str] | None = None,
        time_granularity: str | None = None,
        filters: list[dict[str, Any]] | None = None,
        sort: list[dict[str, Any]] | None = None,
        segments: list[str] | None = None,
        override_segment_group_order: list[str] | None = None,
        includes: list[str] | None = None,
        attribution_window_days: int | None = None,
        view_through_attribution_window_days: int | None = None,
        attribution_time_basis: str | None = None,
        time_range: dict[str, Any] | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> dict[str, Any]:
        path = _scope_path(scope, entity_id)
        params = _insights_params(
            aggregation_level=aggregation_level,
            start_date=start_date,
            end_date=end_date,
            time_range=time_range,
            time_granularity=time_granularity,
            fields=fields,
            filters=filters,
            sort=sort,
            segments=segments,
            override_segment_group_order=override_segment_group_order,
            includes=includes,
            attribution_window_days=attribution_window_days,
            view_through_attribution_window_days=view_through_attribution_window_days,
            attribution_time_basis=attribution_time_basis,
        )
        params["limit"] = max(1, min(limit, 2000))
        if after:
            params["after"] = after
        return fetch_page(path, params)

    @mcp.tool(
        description=(
            "Run an insights report and return all its rows (up to max_rows), following "
            "pagination with progress updates. Same arguments as openai_ads_get_insights. "
            "Rows are held in memory only.\n\n" + GUIDE
        )
    )
    async def openai_ads_run_insights(
        start_date: str | None = None,
        end_date: str | None = None,
        scope: str = "ad_account",
        entity_id: str | None = None,
        aggregation_level: str | None = None,
        fields: list[str] | None = None,
        time_granularity: str | None = None,
        filters: list[dict[str, Any]] | None = None,
        sort: list[dict[str, Any]] | None = None,
        segments: list[str] | None = None,
        override_segment_group_order: list[str] | None = None,
        includes: list[str] | None = None,
        attribution_window_days: int | None = None,
        view_through_attribution_window_days: int | None = None,
        attribution_time_basis: str | None = None,
        time_range: dict[str, Any] | None = None,
        max_rows: int = 5000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        path = _scope_path(scope, entity_id)
        params = _insights_params(
            aggregation_level=aggregation_level,
            start_date=start_date,
            end_date=end_date,
            time_range=time_range,
            time_granularity=time_granularity,
            fields=fields,
            filters=filters,
            sort=sort,
            segments=segments,
            override_segment_group_order=override_segment_group_order,
            includes=includes,
            attribution_window_days=attribution_window_days,
            view_through_attribution_window_days=view_through_attribution_window_days,
            attribution_time_basis=attribution_time_basis,
        )
        rows: list[Any] = []
        page_no = 0
        last: dict[str, Any] = {}
        while True:
            params["limit"] = max(1, min(2000, max_rows - len(rows)))
            last = await run_blocking(fetch_page, path, dict(params))
            raw = last.get("data")
            page: list[Any] = raw if isinstance(raw, list) else []
            rows.extend(page)
            page_no += 1
            await report(ctx, page_no, None, f"Fetched page {page_no}: {len(rows)} rows")
            cursor = last.get("last_id")
            if not last.get("has_more") or not page or not cursor:
                return {"rows": rows, "row_count": len(rows), "pages": page_no, "truncated": False}
            if len(rows) >= max_rows:
                return {
                    "rows": rows[:max_rows],
                    "row_count": max_rows,
                    "pages": page_no,
                    "truncated": True,
                    "next_after": cursor,
                }
            params["after"] = cursor

    def account_unix_range(start: str, end: str) -> dict[str, Any]:
        """Full days in the account timezone as a unix_range (exclusive end)."""
        tz_name = None
        account = client.snapshot(client.request, "GET", "/ad_account")
        if isinstance(account, dict):
            tz_name = account.get("timezone")
        try:
            tz = ZoneInfo(tz_name or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            return date_range(start, end)
        s = datetime.combine(date.fromisoformat(start), time.min, tz)
        e = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time.min, tz)
        return {
            "type": "unix_range",
            "start": str(int(s.timestamp())),
            "end": str(int(e.timestamp())),
        }

    @mcp.tool()
    def openai_ads_get_conversion_insights(
        aggregation_level: str,
        start_date: str,
        end_date: str,
        entity_ids: list[str] | None = None,
        time_granularity: str = "none",
        group_by_entity: bool = True,
        breakdown: str | None = None,
        attribution_window_days: int | None = None,
        view_through_attribution_window_days: int | None = None,
        attribution_time_basis: str | None = None,
        include_attributed_events: bool = False,
        event_names: list[str] | None = None,
        include_zero_rows: bool | None = None,
    ) -> dict[str, Any]:
        """Goal conversions (click-through + view-through) and attributed purchase value by
        campaign, ad group or ad, for full days in the account timezone (up to 365).

        aggregation_level: campaign, ad_group or ad. entity_ids: IDs at that level
        (required unless group_by_entity=false, which totals the account). time_granularity
        none or daily; breakdown country or device. Windows: attribution_window_days 7/14/30
        (default 30, click) and view_through_attribution_window_days 0/1 (default 1).
        include_attributed_events adds per-event details (optionally only event_names).
        Responses over 2,000 rows fail with 413: split the dates or entities."""
        level = check_choice(aggregation_level, ("campaign", "ad_group", "ad"), "aggregation_level")
        start = check_date(start_date, "start_date")
        end = check_date(end_date, "end_date")
        if group_by_entity and not entity_ids:
            raise ValueError("Pass entity_ids, or group_by_entity=false for account totals.")
        if event_names and not include_attributed_events:
            raise ValueError("event_names needs include_attributed_events=true.")
        body = clean(
            {
                "aggregation_level": level,
                "time_ranges": [account_unix_range(start, end)],
                "time_granularity": check_choice(
                    time_granularity, ("none", "daily"), "time_granularity"
                ),
                "entity_ids": entity_ids or None,
                "group_by_entity": group_by_entity,
                "breakdown": check_choice(breakdown, ("country", "device"), "breakdown"),
                "attribution_window_days": attribution_window_days,
                "view_through_attribution_window_days": view_through_attribution_window_days,
                "attribution_time_basis": attribution_time_basis,
                "include": ["attributed_events"] if include_attributed_events else None,
                "event_names": event_names or None,
                "include_zero_rows": include_zero_rows,
            }
        )
        # The API takes each time range as a JSON-encoded string.
        body["time_ranges"] = [json.dumps(r, separators=(",", ":")) for r in body["time_ranges"]]
        return client.request("POST", "/conversions/insights", json_body=body, read_only=True)

    @mcp.tool()
    def openai_ads_get_hotel_insights(
        start_date: str,
        end_date: str,
        timeseries: bool = False,
        campaign_ids: list[str] | None = None,
        ad_group_ids: list[str] | None = None,
        ad_ids: list[str] | None = None,
        time_granularity: str | None = None,
        breakdown: str | None = None,
        limit: int | None = None,
        after: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Hotel property reporting for accounts in the Hotel ads limited beta. Dates are
        YYYY-MM-DD. timeseries=true returns the time series. extra_params passes other
        documented query parameters (e.g. filters, inventory_items, sort_field)."""
        params = clean(
            {
                "start_date": check_date(start_date, "start_date"),
                "end_date": check_date(end_date, "end_date"),
                "campaign_ids": campaign_ids or None,
                "ad_group_ids": ad_group_ids or None,
                "ad_ids": ad_ids or None,
                "time_granularity": time_granularity,
                "breakdown": breakdown,
                "limit": limit,
                "after": after,
            }
        )
        params.update(extra_params or {})
        path = "/ad_account/hotel_insights" + ("/timeseries" if timeseries else "")
        return client.request("GET", path, params=params)
