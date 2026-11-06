"""Confirmation guards for writes that change live ads or spend money.

A write tool asks `assess()` what its change needs, then `refusal()` whether to
stop. A guarded write is refused (with a summary of what it would do) unless the
caller passes the required `confirm` word:

* archives need confirm='ARCHIVE'; deletes (spend limits, lead-sync
  subscriptions) need confirm='DELETE';
* writes touching more than OPENAI_ADS_BULK_THRESHOLD entities, bid, budget or
  spend-limit increases above OPENAI_ADS_MAX_INCREASE_PCT, increases whose
  current value couldn't be read, values over the optional absolute caps, and
  other spend-raising changes (activating the account, removing a bid cap) need
  confirm='APPLY'.

In dry-run mode nothing is sent, so guards don't block; the result says which
confirm a real run would need.

Money values are compared in the account currency (micros / 1,000,000).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .config import Config

APPLY = "APPLY"
ARCHIVE = "ARCHIVE"
DELETE = "DELETE"
MICROS = 1_000_000

# Which absolute cap applies to each kind of money value.
_CAP_FOR = {
    "daily_budget": "max_daily_budget",
    "account_daily_limit": "max_daily_budget",
    "lifetime_budget": "max_lifetime_budget",
    "account_window_limit": "max_lifetime_budget",
    "bid": "max_bid",
}


def micros_to_units(value: Any) -> float | None:
    try:
        return None if value is None else float(value) / MICROS
    except (TypeError, ValueError):
        return None


@dataclass
class MoneyChange:
    """A money value a write sets, in currency units: `old` is None if it couldn't be read.

    kind: daily_budget, lifetime_budget, bid, bid_multiplier (a factor, not
    money), account_daily_limit or account_window_limit.
    """

    entity_id: str
    kind: str
    old: float | None
    new: float


@dataclass
class Decision:
    required: str | None = None
    reasons: list[str] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_confirm": self.required,
            "reasons": self.reasons,
            "money_changes": self.changes[:50],
        }


def assess(
    config: Config,
    *,
    count: int = 1,
    destructive: str | None = None,
    changes: Sequence[MoneyChange] = (),
    creating: bool = False,
    extra_reasons: Sequence[str] = (),
) -> Decision:
    """Work out which confirm word, if any, a write needs."""
    d = Decision()
    if destructive:
        d.required = destructive
        d.reasons.append(f"{destructive.lower()} of {count} item(s) can't be undone by this server")
    if count > config.bulk_threshold:
        d.reasons.append(
            f"{count} items is more than OPENAI_ADS_BULK_THRESHOLD ({config.bulk_threshold})"
        )
    d.reasons.extend(extra_reasons)
    limit = config.max_increase_pct
    for c in changes:
        info: dict[str, Any] = {"id": c.entity_id, "kind": c.kind, "old": c.old, "new": c.new}
        if c.old is not None and c.old > 0:
            info["change_pct"] = round((c.new - c.old) / c.old * 100, 1)
        d.changes.append(info)
        cap_name = _CAP_FOR.get(c.kind)
        cap = getattr(config, cap_name) if cap_name else None
        if cap is not None and c.new > cap:
            env = "OPENAI_ADS_" + cap_name.upper() if cap_name else "cap"
            d.reasons.append(f"{c.kind} {c.new:g} for {c.entity_id} is above {env}={cap:g}")
        if creating:
            continue
        if c.old is None:
            d.reasons.append(
                f"current {c.kind} for {c.entity_id} is unknown, so the change is unchecked"
            )
        elif c.old <= 0 < c.new or (c.old > 0 and (c.new - c.old) / c.old * 100 > limit):
            d.reasons.append(
                f"{c.kind} for {c.entity_id} rises from {c.old:g} to {c.new:g} "
                f"(more than OPENAI_ADS_MAX_INCREASE_PCT={limit:g}%)"
            )
    if d.reasons and d.required is None:
        d.required = APPLY
    return d


def refusal(
    config: Config, decision: Decision, confirm: str, *, preview: Any = None
) -> dict | None:
    """Return a refusal payload if the write must not go ahead, else None."""
    if decision.required is None or config.dry_run or confirm == decision.required:
        return None
    out: dict[str, Any] = {
        "refused": True,
        "error": (
            f"Not sent. Show the user what will change, and once they agree call again "
            f"with confirm='{decision.required}' (case-sensitive)."
        ),
        **decision.as_dict(),
    }
    if preview is not None:
        out["would_send"] = preview
    return out


def annotate(result: Any, decision: Decision) -> Any:
    """Attach the guard summary to a result (useful in dry-run)."""
    if decision.required is None:
        return result
    if isinstance(result, dict):
        return {**result, "safety": decision.as_dict()}
    return {"result": result, "safety": decision.as_dict()}
