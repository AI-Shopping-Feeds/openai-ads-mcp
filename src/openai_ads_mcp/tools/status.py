"""Pause, activate and archive campaigns, ad groups, ads, audiences and feeds."""

from __future__ import annotations

from typing import Any

from .. import safety
from ..auth import redact
from ..client import OpenAIAdsClient
from ._common import ENTITY_PATHS, check_choice, get_entity, ids_list

STATUS_ENTITIES = ("campaign", "ad_group", "ad")
ARCHIVE_ENTITIES = ("campaign", "ad_group", "ad", "custom_audience", "product_feed")
_ACTIONS = {"active": "activate", "paused": "pause"}


def _brief(obj: Any) -> Any:
    if not isinstance(obj, dict):
        return obj
    keep = ("id", "name", "status", "review_status", "budget", "bidding_config")
    return {k: obj[k] for k in keep if k in obj}


def register(mcp, client: OpenAIAdsClient) -> None:
    def run_each(entity: str, ids: list[str], action: str, op: str) -> dict[str, Any]:
        path = ENTITY_PATHS[entity]
        results: list[dict[str, Any]] = []
        for eid in ids:
            before = (
                client.snapshot(get_entity, client, entity, eid)
                if entity != "product_feed"  # feeds have no single-get endpoint
                else None
            )
            try:
                out = client.request(
                    "POST",
                    f"{path}/{eid}/{action}",
                    op=op,
                    before=_brief(before) if before else None,
                    idempotent=True,
                )
                results.append(
                    {
                        "id": eid,
                        "ok": True,
                        "status": out.get("status") if isinstance(out, dict) else None,
                        "audit_id": out.get("audit_id") if isinstance(out, dict) else None,
                        "dry_run": bool(isinstance(out, dict) and out.get("_dry_run")),
                    }
                )
            except Exception as e:  # report per item; keep going
                results.append({"id": eid, "ok": False, "error": redact(str(e))[:500]})
        return {
            "results": results,
            "succeeded": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
        }

    @mcp.tool()
    def openai_ads_set_status(
        entity: str, ids: list[str], status: str, confirm: str = ""
    ) -> dict[str, Any]:
        """Pause or activate campaigns, ad groups or ads by ID. Changes the live account.

        entity: campaign, ad_group or ad. status: paused (stops delivery) or active
        (allows delivery once parents are active, reviews pass and budget remains).
        Results are reported per ID. More than OPENAI_ADS_BULK_THRESHOLD IDs, or
        activating anything, needs confirm='APPLY'. To activate the whole account use
        openai_ads_activate_account."""
        kind = check_choice(entity, STATUS_ENTITIES, "entity") or ""
        target = check_choice(status, tuple(_ACTIONS), "status") or ""
        id_list = ids_list(ids, f"{kind}_id")
        reasons = []
        if target == "active":
            reasons.append(f"activating {len(id_list)} {kind}(s) lets them spend")
        decision = safety.assess(client.config, count=len(id_list), extra_reasons=reasons)
        refused = safety.refusal(
            client.config,
            decision,
            confirm,
            preview={"entity": kind, "ids": id_list, "status": target},
        )
        if refused:
            return refused
        out = run_each(kind, id_list, _ACTIONS[target], f"{_ACTIONS[target]}_{kind}")
        return safety.annotate(out, decision)

    @mcp.tool()
    def openai_ads_archive(entity: str, ids: list[str], confirm: str = "") -> dict[str, Any]:
        """Archive campaigns, ad groups, ads, custom audiences or product feeds by ID.

        entity: campaign, ad_group, ad, custom_audience or product_feed. Archiving stops
        delivery for good: archived items can't be re-activated, so this needs
        confirm='ARCHIVE' after the user agrees. To stop delivery temporarily use
        openai_ads_set_status with status='paused' instead."""
        kind = check_choice(entity, ARCHIVE_ENTITIES, "entity") or ""
        id_list = ids_list(ids, f"{kind}_id")
        decision = safety.assess(client.config, count=len(id_list), destructive=safety.ARCHIVE)
        refused = safety.refusal(
            client.config, decision, confirm, preview={"entity": kind, "ids": id_list}
        )
        if refused:
            return refused
        out = run_each(kind, id_list, "archive", f"archive_{kind}")
        return safety.annotate(out, decision)
