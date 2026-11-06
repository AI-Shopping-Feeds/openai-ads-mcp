"""Shared building blocks for the tool modules."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from .. import safety
from ..client import OpenAIAdsClient, path_id
from ..safety import MICROS, MoneyChange, micros_to_units

# Path of each entity type that has get and archive endpoints.
ENTITY_PATHS = {
    "campaign": "/campaigns",
    "ad_group": "/ad_groups",
    "ad": "/ads",
    "custom_audience": "/custom_audiences",
    "product_feed": "/feeds",
}
MAX_IDS = 200
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean(d: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None."""
    return {k: v for k, v in d.items() if v is not None}


def units_to_micros(value: float | int | None, what: str) -> int | None:
    """Convert an amount in the account currency (e.g. 12.5) to micros (12500000)."""
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{what} must be a number in the account currency.") from e
    if amount < 0:
        raise ValueError(f"{what} can't be negative.")
    return round(amount * MICROS)


def to_unix(value: Any, what: str) -> int | None:
    """Unix seconds from an int or an ISO 8601 date-time with a UTC offset."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{what} must be Unix seconds or an ISO 8601 date-time.")
    if isinstance(value, int | float):
        return int(value)
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(
            f"{what} must be Unix seconds or an ISO 8601 date-time such as "
            "'2026-12-01T00:00:00-05:00'."
        ) from e
    if parsed.tzinfo is None:
        raise ValueError(
            f"{what} needs a UTC offset (e.g. '2026-12-01T00:00:00-05:00' or a trailing 'Z'), "
            "because a bare date is ambiguous; use the ad account's timezone."
        )
    return int(parsed.timestamp())


def check_date(value: str, what: str) -> str:
    text = (value or "").strip()
    if not _DATE_RE.match(text):
        raise ValueError(f"{what} must be YYYY-MM-DD, got '{value}'.")
    try:
        date.fromisoformat(text)
    except ValueError as e:
        raise ValueError(f"{what} is not a real date: '{value}'.") from e
    return text


def date_range(start_date: str, end_date: str) -> dict[str, str]:
    """An insights time range covering start_date..end_date inclusive (account timezone)."""
    since = check_date(start_date, "start_date")
    until = check_date(end_date, "end_date")
    if until < since:
        raise ValueError("end_date is before start_date.")
    return {"type": "date_range", "since": since, "until": until}


def check_choice(value: str | None, allowed: Sequence[str], what: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text not in allowed:
        raise ValueError(f"{what} must be one of {', '.join(allowed)}, got '{value}'.")
    return text


def ids_list(ids: Sequence[Any] | None, what: str = "id", max_items: int = MAX_IDS) -> list[str]:
    out = [path_id(i, what) for i in (ids or []) if str(i).strip()]
    if not out:
        raise ValueError(f"Pass at least one {what}.")
    if len(out) > max_items:
        raise ValueError(f"At most {max_items} IDs per call; got {len(out)}.")
    return out


def get_entity(
    client: OpenAIAdsClient, entity: str, entity_id: str, include: list[str] | None = None
) -> dict[str, Any]:
    path = ENTITY_PATHS[entity]
    params = {"include": include} if include else None
    out = client.request("GET", f"{path}/{path_id(entity_id, f'{entity}_id')}", params=params)
    return out if isinstance(out, dict) else {}


def budget_changes(
    entity_id: str, current: dict[str, Any] | None, new_budget: dict[str, Any] | None
) -> list[MoneyChange]:
    """Compare a campaign budget object with the current one (values in currency units)."""
    if not new_budget:
        return []
    old_budget = (current or {}).get("budget") if current is not None else None
    out: list[MoneyChange] = []
    for field, kind in (
        ("daily_spend_limit_micros", "daily_budget"),
        ("lifetime_spend_limit_micros", "lifetime_budget"),
    ):
        new = micros_to_units(new_budget.get(field))
        if new is None:
            continue
        old = micros_to_units(old_budget.get(field)) if isinstance(old_budget, dict) else None
        out.append(MoneyChange(entity_id, kind, old, new))
    return out


def bid_changes(
    entity_id: str, current: dict[str, Any] | None, new_config: dict[str, Any] | None
) -> tuple[list[MoneyChange], list[str]]:
    """Compare an ad group's bidding_config with the current one.

    Returns money changes (max bid and audience multipliers) and extra reasons
    that need confirm='APPLY' (e.g. switching to automated bidding removes the cap).
    """
    if not new_config:
        return [], []
    old_config = (current or {}).get("bidding_config") if current is not None else None
    old_config = old_config if isinstance(old_config, dict) else None
    changes: list[MoneyChange] = []
    reasons: list[str] = []
    new_bid = micros_to_units(new_config.get("max_bid_micros"))
    if new_bid is not None:
        old_bid = micros_to_units(old_config.get("max_bid_micros")) if old_config else None
        changes.append(MoneyChange(entity_id, "bid", old_bid, new_bid))
    new_strategy = new_config.get("strategy")
    old_strategy = (old_config or {}).get("strategy") or (
        "fixed_bid" if old_config and old_config.get("max_bid_micros") else None
    )
    if new_strategy in {"maximize_clicks", "maximize_conversions"} and (
        old_strategy != new_strategy
    ):
        reasons.append(
            f"{entity_id} switches to {new_strategy}: OpenAI sets bids within the daily "
            "budget, with no max bid cap"
        )
    old_mult = {
        str(m.get("custom_audience_id")): m.get("bid_multiplier_micros")
        for m in ((old_config or {}).get("custom_audience_bid_multipliers") or [])
        if isinstance(m, dict)
    }
    for m in new_config.get("custom_audience_bid_multipliers") or []:
        if not isinstance(m, dict):
            continue
        aud = str(m.get("custom_audience_id"))
        new_factor = micros_to_units(m.get("bid_multiplier_micros"))
        if new_factor is None:
            continue
        old_factor = micros_to_units(old_mult.get(aud)) if old_config is not None else None
        if old_config is not None and aud not in old_mult:
            old_factor = 1.0  # no multiplier means 1x
        changes.append(MoneyChange(f"{entity_id}/{aud}", "bid_multiplier", old_factor, new_factor))
    return changes, reasons


def multipliers_to_api(items: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """[{custom_audience_id, multiplier: 1.5}] -> API shape with bid_multiplier_micros."""
    if items is None:
        return None
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("custom_audience_id"):
            raise ValueError(f"audience_bid_multipliers[{i}] needs custom_audience_id.")
        if "bid_multiplier_micros" in item:
            micros = int(item["bid_multiplier_micros"])
        else:
            factor = float(item.get("multiplier", 0))
            micros = round(factor * MICROS)
        if not 100_000 <= micros <= 10_000_000:
            raise ValueError(
                f"audience_bid_multipliers[{i}]: multiplier must be between 0.1 and 10."
            )
        out.append(
            {"custom_audience_id": str(item["custom_audience_id"]), "bid_multiplier_micros": micros}
        )
    return out


def guarded_write(
    client: OpenAIAdsClient,
    decision: safety.Decision,
    confirm: str,
    preview: Any,
    send: Any,
) -> Any:
    """Refuse without the confirm word, otherwise run `send()` and annotate the result."""
    refused = safety.refusal(client.config, decision, confirm, preview=preview)
    if refused:
        return refused
    return safety.annotate(send(), decision)


def with_money_view(obj: Any) -> Any:
    """Add currency-unit readings next to *_micros fields at the top two levels."""
    if not isinstance(obj, dict):
        return obj
    out = dict(obj)
    for key in ("budget", "bidding_config"):
        inner = out.get(key)
        if isinstance(inner, dict):
            view = {
                k.removesuffix("_micros"): micros_to_units(v)
                for k, v in inner.items()
                if k.endswith("_micros") and isinstance(v, int | float)
            }
            if view:
                out[f"{key}_in_currency"] = view
    return out
