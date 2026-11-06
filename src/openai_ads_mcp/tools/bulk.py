"""Bulk API (limited preview, enabled per account): create or update up to 1,000
campaigns, ad groups and ads in one asynchronous job.

Not in OpenAI's downloadable OpenAPI spec; built from the Bulk API guide. Bulk
inputs differ from the single-resource bodies (e.g. campaign max_budget_micros).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mcp.server.mcpserver import Context

from .. import safety
from ..client import OpenAIAdsClient, path_id
from ._progress import report, run_blocking

OP_TYPES = (
    "campaign.create",
    "campaign.update",
    "ad_group.create",
    "ad_group.update",
    "ad.create",
    "ad.update",
)
TERMINAL = {"completed", "partially_failed", "failed"}
MAX_OPERATIONS = 1000


def _check_operations(operations: list[dict[str, Any]]) -> None:
    if not operations:
        raise ValueError("Pass at least one operation.")
    if len(operations) > MAX_OPERATIONS:
        raise ValueError(f"At most {MAX_OPERATIONS} operations per job; got {len(operations)}.")
    seen: set[str] = set()
    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            raise ValueError(f"operations[{i}] must be an object.")
        oid = op.get("operation_id")
        if not oid or oid in seen:
            raise ValueError(f"operations[{i}] needs a unique operation_id.")
        seen.add(oid)
        if op.get("type") not in OP_TYPES:
            raise ValueError(f"operations[{i}].type must be one of {', '.join(OP_TYPES)}.")
        if not isinstance(op.get("input"), dict) or not op["input"]:
            raise ValueError(f"operations[{i}] needs a non-empty input object.")
        if op["type"].endswith(".create") and not op.get("idempotency_key"):
            raise ValueError(f"operations[{i}] is a create and needs an idempotency_key.")
        if op["type"].endswith(".update") and not op.get("target_resource_id"):
            raise ValueError(f"operations[{i}] is an update and needs target_resource_id.")


def register(mcp, client: OpenAIAdsClient) -> None:
    def get_job(job_id: str) -> dict[str, Any]:
        out = client.request("GET", f"/bulk_mutation_jobs/{path_id(job_id, 'job_id')}")
        return out if isinstance(out, dict) else {}

    def results(job_id: str, max_results: int) -> dict[str, Any]:
        jid = path_id(job_id, "job_id")
        items: list[Any] = []
        params: dict[str, Any] = {"limit": 100}
        complete = False
        while True:
            page = client.request("GET", f"/bulk_mutation_jobs/{jid}/operations", params=params)
            page = page if isinstance(page, dict) else {}
            raw = page.get("data")
            data: list[Any] = raw if isinstance(raw, list) else []
            items.extend(data)
            complete = bool(page.get("complete"))
            if not (complete and page.get("has_more") and data) or len(items) >= max_results:
                break
            params["after"] = data[-1].get("operation_id")
        summary: dict[str, int] = {}
        for r in items:
            status = str(r.get("status")) if isinstance(r, dict) else "?"
            summary[status] = summary.get(status, 0) + 1
        return {
            "complete": complete,
            "summary": summary,
            "data": items[:max_results],
            "truncated": len(items) > max_results,
            "error": page.get("error"),
        }

    @mcp.tool()
    async def openai_ads_submit_bulk_job(
        operations: list[dict[str, Any]],
        validate_only: bool = True,
        partial_failure: bool = True,
        wait: bool = True,
        timeout_seconds: float = 300,
        poll_seconds: float = 5,
        idempotency_key: str | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Submit a Bulk API job (limited preview; 404 means it isn't enabled for the account).

        operations: up to 1,000 objects, each {"operation_id": unique, "type": one of
        campaign.create, campaign.update, ad_group.create, ad_group.update, ad.create,
        ad.update, "input": {...}}, plus "idempotency_key" for creates and
        "target_resource_id" for updates. Bulk inputs differ from the single-resource
        tools: campaign.create takes name, max_budget_micros, budget_type, billing_event_type,
        status, target_countries, location_ids; ad_group.create takes
        campaign_idempotency_key, name, max_bid_micros or max_cpm_bid_micros,
        context_hints; ad.create takes campaign_idempotency_key, ad_group_idempotency_key,
        title, body, target_url, source_image_url. Creates can reference parents made in the
        same job; updates can't target them.

        validate_only defaults to TRUE here: the job checks fields and dependencies but
        changes nothing. Set validate_only=false to apply; that needs confirm='APPLY',
        because the bids and budgets inside a job aren't checked one by one. Nothing rolls
        back completed operations. With wait=true the job is polled (with progress) until
        it finishes or timeout_seconds passes, and the operation results are returned."""
        _check_operations(operations)
        body = {
            "operations": operations,
            "validate_only": validate_only,
            "partial_failure": partial_failure,
        }
        key = idempotency_key or client.new_idempotency_key("bulk-job")
        if not validate_only:
            decision = safety.Decision(
                required=safety.APPLY,
                reasons=[
                    f"bulk job with {len(operations)} operation(s); bids and budgets inside "
                    "it aren't checked one by one"
                ],
            )
            refused = safety.refusal(
                client.config,
                decision,
                confirm,
                preview={"operations": len(operations), "first": operations[:5]},
            )
            if refused:
                return refused
        created = await run_blocking(
            client.request,
            "POST",
            "/bulk_mutation_jobs",
            json_body=body,
            op="submit_bulk_job",
            idempotency_key=key,
            read_only=validate_only,
        )
        if not isinstance(created, dict) or created.get("_dry_run") or not wait:
            return created
        job_id = created.get("id")
        if not job_id:
            return created
        step = 1
        await report(ctx, step, None, f"Submitted job {job_id}")
        deadline = time.monotonic() + timeout_seconds
        job = created
        while job.get("status") not in TERMINAL:
            if time.monotonic() >= deadline:
                return {
                    "job": job,
                    "message": "Still running. Check later with openai_ads_get_bulk_job.",
                }
            await asyncio.sleep(max(0.0, poll_seconds))
            job = await run_blocking(get_job, job_id)
            step += 1
            await report(ctx, step, None, f"Job {job_id}: {job.get('status')}")
        out = await run_blocking(results, job_id, 1000)
        await report(ctx, step + 1, None, "Fetched operation results")
        return {"job": job, "results": out, "idempotency_key": key}

    @mcp.tool()
    def openai_ads_get_bulk_job(job_id: str) -> dict[str, Any]:
        """Check a bulk job: pending, in_progress, completed, partially_failed or failed."""
        return get_job(job_id)

    @mcp.tool()
    def openai_ads_list_bulk_job_results(job_id: str, max_results: int = 1000) -> dict[str, Any]:
        """List each operation's result in a bulk job (created, updated, validated, failed,
        skipped) with error details and retry hints, plus a count by status. While the job
        runs this can be an incomplete snapshot."""
        return results(job_id, max_results)
