from __future__ import annotations

import dataclasses

from openai_ads_mcp import safety
from openai_ads_mcp.config import Config
from openai_ads_mcp.safety import MoneyChange


def test_small_change_needs_nothing(config: Config) -> None:
    d = safety.assess(config, changes=[MoneyChange("c", "daily_budget", 100, 110)])
    assert d.required is None
    assert d.changes[0]["change_pct"] == 10.0


def test_rise_above_limit_needs_apply(config: Config) -> None:
    d = safety.assess(config, changes=[MoneyChange("c", "daily_budget", 100, 121)])
    assert d.required == safety.APPLY
    assert "OPENAI_ADS_MAX_INCREASE_PCT=20%" in d.reasons[0]


def test_unknown_current_value_needs_apply(config: Config) -> None:
    d = safety.assess(config, changes=[MoneyChange("g", "bid", None, 2)])
    assert d.required == safety.APPLY
    assert "unknown" in d.reasons[0]


def test_decreases_pass(config: Config) -> None:
    d = safety.assess(config, changes=[MoneyChange("g", "bid", 5, 1)])
    assert d.required is None


def test_caps_apply_to_creates(config: Config) -> None:
    capped = dataclasses.replace(config, max_daily_budget=100, max_bid=2, max_lifetime_budget=500)
    assert (
        safety.assess(
            capped, changes=[MoneyChange("n", "daily_budget", None, 50)], creating=True
        ).required
        is None
    )
    d = safety.assess(capped, changes=[MoneyChange("n", "daily_budget", None, 150)], creating=True)
    assert d.required == safety.APPLY and "OPENAI_ADS_MAX_DAILY_BUDGET" in d.reasons[0]
    assert (
        safety.assess(capped, changes=[MoneyChange("n", "bid", None, 3)], creating=True).required
        == safety.APPLY
    )
    assert (
        safety.assess(
            capped, changes=[MoneyChange("n", "account_window_limit", None, 900)], creating=True
        ).required
        == safety.APPLY
    )


def test_bulk_threshold(config: Config) -> None:
    assert safety.assess(config, count=10).required is None
    assert safety.assess(config, count=11).required == safety.APPLY


def test_destructive_word_wins(config: Config) -> None:
    d = safety.assess(config, count=50, destructive=safety.ARCHIVE)
    assert d.required == safety.ARCHIVE


def test_refusal_and_dry_run(config: Config) -> None:
    d = safety.assess(config, extra_reasons=["activating spends money"])
    refused = safety.refusal(config, d, "", preview={"x": 1})
    assert refused and refused["refused"] is True and refused["would_send"] == {"x": 1}
    assert safety.refusal(config, d, "apply") is not None  # case-sensitive
    assert safety.refusal(config, d, "APPLY") is None
    assert safety.refusal(dataclasses.replace(config, dry_run=True), d, "") is None
    assert safety.annotate({"ok": 1}, d)["safety"]["required_confirm"] == "APPLY"
