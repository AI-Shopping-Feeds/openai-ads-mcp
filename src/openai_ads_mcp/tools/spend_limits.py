"""Account-wide spending limits: date-range windows and a renewing daily limit.

Available only to ad accounts on postpaid invoice billing, and every call (reads
included) needs permission to manage billing. Campaign budgets still apply on
top. Amounts here are in the account currency; the API takes micros.
"""

from __future__ import annotations

from typing import Any

from .. import safety
from ..client import OpenAIAdsClient, path_id
from ..safety import MoneyChange, micros_to_units
from ._common import check_date, clean, guarded_write, units_to_micros

PATH = "/ad_account/spend_limit_windows"


def _with_units(payload: Any) -> Any:
    """Add amount_in_currency next to amount_micros in windows and the daily limit."""
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    for key in ("data",):
        if isinstance(out.get(key), list):
            out[key] = [
                {**w, "amount_in_currency": micros_to_units(w.get("amount_micros"))}
                if isinstance(w, dict)
                else w
                for w in out[key]
            ]
    daily = out.get("daily_limit")
    if isinstance(daily, dict):
        out["daily_limit"] = {
            **daily,
            "amount_in_currency": micros_to_units(daily.get("amount_micros")),
        }
    return out


def register(mcp, client: OpenAIAdsClient) -> None:
    def get_limits() -> dict[str, Any]:
        out = client.request("GET", PATH)
        return out if isinstance(out, dict) else {}

    def find_window(limits: dict[str, Any] | None, window_id: str) -> dict[str, Any] | None:
        for w in (limits or {}).get("data") or []:
            if isinstance(w, dict) and str(w.get("window_id")) == window_id:
                return w
        return None

    @mcp.tool()
    def openai_ads_get_spend_limits() -> dict[str, Any]:
        """Show the account's spending limits: date-range windows (data), the daily limit
        (daily_limit), the revision to send with daily-limit changes, and what can be
        created or edited. Amounts are shown in micros and in the account currency.

        Only for accounts on postpaid invoice billing; the key needs billing permission."""
        return _with_units(get_limits())

    @mcp.tool()
    def openai_ads_create_spend_limit_window(
        start_date: str,
        end_date: str,
        amount: float,
        name: str | None = None,
        io_id: str | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Create a date-range spending limit: a total allowance across all campaigns for
        start_date (inclusive) to end_date (exclusive), YYYY-MM-DD in the account timezone.

        amount is in the account currency (e.g. 1000 for 1,000 USD). Windows can't overlap
        each other or a daily limit; up to 60 future windows. An amount above
        OPENAI_ADS_MAX_LIFETIME_BUDGET needs confirm='APPLY'."""
        micros = units_to_micros(amount, "amount")
        body = clean(
            {
                "start_date": check_date(start_date, "start_date"),
                "end_date": check_date(end_date, "end_date"),
                "amount_micros": micros,
                "name": name,
                "io_id": io_id,
            }
        )
        decision = safety.assess(
            client.config,
            changes=[MoneyChange("new_window", "account_window_limit", None, float(amount))],
            creating=True,
        )
        return guarded_write(
            client,
            decision,
            confirm,
            body,
            lambda: client.request("POST", PATH, json_body=body, op="create_spend_limit_window"),
        )

    @mcp.tool()
    def openai_ads_update_spend_limit_window(
        window_id: str,
        amount: float | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        name: str | None = None,
        io_id: str | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Change a date-range spending limit. Send only what changes.

        amount is the new total in the account currency; it can't go below spend already
        recorded. Only upcoming windows can change start_date; completed windows can't be
        edited. Raising the amount more than OPENAI_ADS_MAX_INCREASE_PCT (or when the
        current amount can't be read) needs confirm='APPLY'."""
        wid = path_id(window_id, "window_id")
        body = clean(
            {
                "amount_micros": units_to_micros(amount, "amount"),
                "start_date": check_date(start_date, "start_date") if start_date else None,
                "end_date": check_date(end_date, "end_date") if end_date else None,
                "name": name,
                "io_id": io_id,
            }
        )
        if not body:
            raise ValueError("Pass at least one field to change.")
        limits = client.snapshot(get_limits)
        current = find_window(limits, wid)
        changes = []
        if amount is not None:
            old = micros_to_units(current.get("amount_micros")) if current else None
            changes.append(MoneyChange(wid, "account_window_limit", old, float(amount)))
        decision = safety.assess(client.config, changes=changes)
        return guarded_write(
            client,
            decision,
            confirm,
            body,
            lambda: client.request(
                "POST",
                f"{PATH}/{wid}",
                json_body=body,
                op="update_spend_limit_window",
                before=current,
                idempotent=True,
            ),
        )

    @mcp.tool()
    def openai_ads_delete_spend_limit_window(window_id: str, confirm: str = "") -> dict[str, Any]:
        """Delete an active or upcoming date-range spending limit. Removing an exhausted
        limit can let delivery (and spend) resume, so this needs confirm='DELETE' after
        the user agrees. Completed windows can't be deleted."""
        wid = path_id(window_id, "window_id")
        decision = safety.assess(client.config, destructive=safety.DELETE)
        current = find_window(client.snapshot(get_limits), wid)
        return guarded_write(
            client,
            decision,
            confirm,
            {"method": "POST", "path": f"{PATH}/{wid}/delete", "window": current},
            lambda: client.request(
                "POST",
                f"{PATH}/{wid}/delete",
                op="delete_spend_limit_window",
                before=current,
                idempotent=True,
            ),
        )

    @mcp.tool()
    def openai_ads_set_daily_spend_limit(
        amount: float,
        start_date: str | None = None,
        end_date: str | None = None,
        remove_end_date: bool = False,
        expected_revision: int | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Create or change the account's daily spending limit (an allowance shared by all
        campaigns that renews at midnight in the account timezone).

        amount is per day in the account currency. A new limit starts tomorrow or later
        (start_date defaults to the earliest allowed day); start_date can't change once
        set. end_date (YYYY-MM-DD, exclusive) is optional; remove_end_date=true makes it
        open-ended. The current revision is read for you unless you pass
        expected_revision. Raising the amount more than OPENAI_ADS_MAX_INCREASE_PCT, or
        above OPENAI_ADS_MAX_DAILY_BUDGET, needs confirm='APPLY'."""
        limits = client.snapshot(get_limits)
        revision = expected_revision
        if revision is None:
            if limits is None:
                raise RuntimeError(
                    "Couldn't read the current spend limits to get their revision; pass "
                    "expected_revision from openai_ads_get_spend_limits."
                )
            revision = int(limits.get("revision") or 0)
        current = (limits or {}).get("daily_limit")
        body: dict[str, Any] = {
            "amount_micros": units_to_micros(amount, "amount"),
            "expected_revision": revision,
        }
        if start_date and not current:
            body["start_date"] = check_date(start_date, "start_date")
        elif start_date and current:
            raise ValueError("An existing daily limit's start_date can't change; omit it.")
        if remove_end_date:
            body["end_date"] = None
        elif end_date:
            body["end_date"] = check_date(end_date, "end_date")
        old = micros_to_units(current.get("amount_micros")) if isinstance(current, dict) else None
        creating = not current and limits is not None
        decision = safety.assess(
            client.config,
            changes=[MoneyChange("daily_limit", "account_daily_limit", old, float(amount))],
            creating=creating,
        )
        return guarded_write(
            client,
            decision,
            confirm,
            body,
            lambda: _with_units(
                client.request(
                    "POST",
                    "/ad_account/daily_spend_limit",
                    json_body=body,
                    op="set_daily_spend_limit",
                    before=current,
                    idempotent=True,
                )
            ),
        )

    @mcp.tool()
    def openai_ads_delete_daily_spend_limit(
        expected_revision: int | None = None, confirm: str = ""
    ) -> dict[str, Any]:
        """Remove the account's daily spending limit. Removing a cap can let spend resume,
        so this needs confirm='DELETE' after the user agrees. The current revision is
        read for you unless you pass expected_revision."""
        limits = client.snapshot(get_limits)
        revision = expected_revision
        if revision is None:
            if limits is None:
                raise RuntimeError(
                    "Couldn't read the current spend limits to get their revision; pass "
                    "expected_revision from openai_ads_get_spend_limits."
                )
            revision = int(limits.get("revision") or 0)
        current = (limits or {}).get("daily_limit")
        body = {"expected_revision": revision}
        decision = safety.assess(client.config, destructive=safety.DELETE)
        return guarded_write(
            client,
            decision,
            confirm,
            {**body, "daily_limit": current},
            lambda: client.request(
                "POST",
                "/ad_account/daily_spend_limit/delete",
                json_body=body,
                op="delete_daily_spend_limit",
                before=current,
                idempotent=True,
            ),
        )
