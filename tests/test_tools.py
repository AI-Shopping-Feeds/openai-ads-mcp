"""Tool behaviour through the MCP client: guards, money conversion, merges, secrets."""

from __future__ import annotations

import dataclasses
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp import Client
from mcp.server.mcpserver import MCPServer

from openai_ads_mcp import server
from openai_ads_mcp.config import Config

from .conftest import BASE, audit_entries


def _build(config: Config, **changes: Any) -> MCPServer:
    return server.build_server(dataclasses.replace(config, **changes))


async def call(mcp: MCPServer, name: str, args: dict[str, Any] | None = None) -> Any:
    async with Client(mcp) as c:
        result = await c.call_tool(name, args or {})
    text = " ".join(getattr(b, "text", "") for b in result.content)
    if result.is_error:
        raise AssertionError(f"{name} failed: {text}")
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(text)


async def call_error(mcp: MCPServer, name: str, args: dict[str, Any]) -> str:
    async with Client(mcp) as c:
        result = await c.call_tool(name, args)
    assert result.is_error, f"{name} should have failed"
    return " ".join(getattr(b, "text", "") for b in result.content)


def _body(route: respx.Route, i: int = -1) -> Any:
    return json.loads(route.calls[i].request.content)


# ---------------------------------------------------------------- campaigns


@respx.mock
async def test_create_campaign_defaults_to_paused_and_converts_money(config: Config) -> None:
    route = respx.post(f"{BASE}/campaigns").mock(
        return_value=httpx.Response(
            200, json={"id": "cmpn_1", "budget": {"daily_spend_limit_micros": 50_000_000}}
        )
    )
    out = await call(
        _build(config),
        "openai_ads_create_campaign",
        {"name": "Spring", "bidding_type": "clicks", "daily_budget": 50, "countries": ["us"]},
    )
    body = _body(route)
    assert body["status"] == "paused"
    assert body["budget"] == {"daily_spend_limit_micros": 50_000_000}
    assert body["targeting"] == {"locations": {"countries": ["US"]}}
    assert route.calls.last.request.headers["Idempotency-Key"].startswith("campaign-")
    assert out["budget_in_currency"] == {"daily_spend_limit": 50.0}


async def test_create_active_campaign_needs_apply(config: Config) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{BASE}/campaigns").mock(return_value=httpx.Response(200, json={}))
        out = await call(
            _build(config),
            "openai_ads_create_campaign",
            {"name": "Live", "bidding_type": "clicks", "daily_budget": 5, "status": "active"},
        )
    assert out["refused"] is True and out["required_confirm"] == "APPLY"
    assert route.call_count == 0


async def test_budget_cap_on_create(config: Config) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE}/campaigns").mock(return_value=httpx.Response(200, json={}))
        out = await call(
            _build(config, max_daily_budget=100),
            "openai_ads_create_campaign",
            {"name": "Big", "bidding_type": "clicks", "daily_budget": 500},
        )
    assert out["refused"] is True
    assert "OPENAI_ADS_MAX_DAILY_BUDGET" in out["reasons"][0]


@respx.mock
async def test_update_campaign_budget_rise_needs_apply_then_sends(
    config: Config, tmp_audit_log: Path
) -> None:
    respx.get(f"{BASE}/campaigns/cmpn_1").mock(
        return_value=httpx.Response(
            200, json={"id": "cmpn_1", "budget": {"daily_spend_limit_micros": 50_000_000}}
        )
    )
    post = respx.post(f"{BASE}/campaigns/cmpn_1").mock(
        return_value=httpx.Response(200, json={"id": "cmpn_1"})
    )
    mcp = _build(config)
    refused = await call(
        mcp, "openai_ads_update_campaign", {"campaign_id": "cmpn_1", "daily_budget": 80}
    )
    assert refused["refused"] is True
    assert refused["money_changes"][0]["change_pct"] == 60.0
    assert post.call_count == 0
    ok = await call(
        mcp,
        "openai_ads_update_campaign",
        {"campaign_id": "cmpn_1", "daily_budget": 80, "confirm": "APPLY"},
    )
    assert _body(post) == {"budget": {"daily_spend_limit_micros": 80_000_000}}
    entry = audit_entries(tmp_audit_log)[-1]
    assert entry["before"]["budget"]["daily_spend_limit_micros"] == 50_000_000
    assert ok["safety"]["required_confirm"] == "APPLY"


@respx.mock
async def test_small_budget_change_needs_no_confirm(config: Config) -> None:
    respx.get(f"{BASE}/campaigns/cmpn_1").mock(
        return_value=httpx.Response(200, json={"budget": {"daily_spend_limit_micros": 50_000_000}})
    )
    post = respx.post(f"{BASE}/campaigns/cmpn_1").mock(return_value=httpx.Response(200, json={}))
    await call(
        _build(config), "openai_ads_update_campaign", {"campaign_id": "cmpn_1", "daily_budget": 55}
    )
    assert post.call_count == 1


async def test_bad_ids_are_rejected(config: Config) -> None:
    message = await call_error(
        _build(config), "openai_ads_update_campaign", {"campaign_id": "cmpn_1/archive", "name": "x"}
    )
    assert "not a valid ID" in message


# ---------------------------------------------------------------- ad groups


@respx.mock
async def test_update_ad_group_bid_merges_config_and_guards(config: Config) -> None:
    respx.get(f"{BASE}/ad_groups/adgrp_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "adgrp_1",
                "bidding_config": {
                    "billing_event_type": "click",
                    "strategy": "fixed_bid",
                    "max_bid_micros": 2_000_000,
                },
            },
        )
    )
    post = respx.post(f"{BASE}/ad_groups/adgrp_1").mock(return_value=httpx.Response(200, json={}))
    mcp = _build(config)
    out = await call(mcp, "openai_ads_update_ad_group", {"ad_group_id": "adgrp_1", "max_bid": 3})
    assert out["refused"] is True  # +50%
    await call(mcp, "openai_ads_update_ad_group", {"ad_group_id": "adgrp_1", "max_bid": 2.2})
    assert _body(post)["bidding_config"] == {
        "billing_event_type": "click",
        "strategy": "fixed_bid",
        "max_bid_micros": 2_200_000,
    }


@respx.mock
async def test_switch_to_maximize_drops_bid_and_needs_apply(config: Config) -> None:
    respx.get(f"{BASE}/ad_groups/adgrp_1").mock(
        return_value=httpx.Response(
            200,
            json={"bidding_config": {"billing_event_type": "click", "max_bid_micros": 2_000_000}},
        )
    )
    post = respx.post(f"{BASE}/ad_groups/adgrp_1").mock(return_value=httpx.Response(200, json={}))
    mcp = _build(config)
    out = await call(
        mcp, "openai_ads_update_ad_group", {"ad_group_id": "adgrp_1", "strategy": "maximize_clicks"}
    )
    assert out["refused"] is True and "no max bid cap" in out["reasons"][0]
    await call(
        mcp,
        "openai_ads_update_ad_group",
        {"ad_group_id": "adgrp_1", "strategy": "maximize_clicks", "confirm": "APPLY"},
    )
    assert _body(post)["bidding_config"] == {
        "billing_event_type": "click",
        "strategy": "maximize_clicks",
        "custom_audience_bid_multipliers": [],
    }


@respx.mock
async def test_create_ad_group_fixed_bid_in_micros(config: Config) -> None:
    route = respx.post(f"{BASE}/ad_groups").mock(return_value=httpx.Response(200, json={}))
    await call(
        _build(config),
        "openai_ads_create_ad_group",
        {
            "campaign_id": "cmpn_1",
            "name": "Trail",
            "billing_event_type": "impression",
            "max_bid": 0.06,
            "audience_bid_multipliers": [{"custom_audience_id": "caud_1", "multiplier": 1.5}],
        },
    )
    body = _body(route)
    assert body["status"] == "paused"
    assert body["bidding_config"]["max_bid_micros"] == 60_000
    assert body["bidding_config"]["custom_audience_bid_multipliers"] == [
        {"custom_audience_id": "caud_1", "bid_multiplier_micros": 1_500_000}
    ]


@respx.mock
async def test_list_ad_groups_across_campaigns(config: Config) -> None:
    respx.get(f"{BASE}/campaigns").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"id": "c1", "status": "active"}, {"id": "c2", "status": "archived"}]},
        )
    )
    groups = respx.get(f"{BASE}/ad_groups").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "g1"}], "has_more": False})
    )
    out = await call(_build(config), "openai_ads_list_ad_groups", {})
    assert out["data"] == [{"id": "g1", "campaign_id": "c1"}]
    assert groups.call_count == 1  # archived campaign skipped


# ---------------------------------------------------------------- ads


@respx.mock
async def test_update_ad_merges_creative(config: Config) -> None:
    respx.get(f"{BASE}/ads/ad_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "creative": {
                    "type": "chat_card",
                    "title": "Old title",
                    "body": "Old body",
                    "file_id": "file_1",
                    "image_url": "https://cdn/x.png",
                    "target_url": "https://example.com",
                }
            },
        )
    )
    post = respx.post(f"{BASE}/ads/ad_1").mock(return_value=httpx.Response(200, json={}))
    out = await call(
        _build(config), "openai_ads_update_ad", {"ad_id": "ad_1", "title": "New title"}
    )
    assert _body(post)["creative"] == {
        "type": "chat_card",
        "title": "New title",
        "body": "Old body",
        "target_url": "https://example.com",
        "file_id": "file_1",
    }
    assert "review" in out["note"]


async def test_create_ad_validates_copy(config: Config) -> None:
    message = await call_error(
        _build(config),
        "openai_ads_create_ad",
        {
            "ad_group_id": "adgrp_1",
            "name": "Ad",
            "title": "x" * 60,
            "body": "b",
            "target_url": "https://e.com",
            "file_id": "f",
        },
    )
    assert "3-50" in message


# ---------------------------------------------------------------- status


@respx.mock
async def test_pause_one_needs_no_confirm(config: Config) -> None:
    respx.get(f"{BASE}/ads/ad_1").mock(return_value=httpx.Response(200, json={"status": "active"}))
    pause = respx.post(f"{BASE}/ads/ad_1/pause").mock(
        return_value=httpx.Response(200, json={"status": "paused"})
    )
    out = await call(
        _build(config),
        "openai_ads_set_status",
        {"entity": "ad", "ids": ["ad_1"], "status": "paused"},
    )
    assert out["succeeded"] == 1 and pause.call_count == 1


async def test_activate_and_bulk_need_apply(config: Config) -> None:
    mcp = _build(config)
    with respx.mock(assert_all_called=False):
        out = await call(
            mcp, "openai_ads_set_status", {"entity": "campaign", "ids": ["c1"], "status": "active"}
        )
        assert out["required_confirm"] == "APPLY"
        many = [f"c{i}" for i in range(11)]
        out = await call(
            mcp, "openai_ads_set_status", {"entity": "campaign", "ids": many, "status": "paused"}
        )
        assert out["required_confirm"] == "APPLY"


@respx.mock
async def test_archive_needs_archive_word(config: Config) -> None:
    respx.get(f"{BASE}/campaigns/c1").mock(return_value=httpx.Response(200, json={"id": "c1"}))
    archive = respx.post(f"{BASE}/campaigns/c1/archive").mock(
        return_value=httpx.Response(200, json={"status": "archived"})
    )
    mcp = _build(config)
    out = await call(
        mcp, "openai_ads_archive", {"entity": "campaign", "ids": ["c1"], "confirm": "APPLY"}
    )
    assert out["required_confirm"] == "ARCHIVE" and archive.call_count == 0
    out = await call(
        mcp, "openai_ads_archive", {"entity": "campaign", "ids": ["c1"], "confirm": "ARCHIVE"}
    )
    assert out["succeeded"] == 1 and archive.call_count == 1


# ---------------------------------------------------------------- spend limits


@respx.mock
async def test_daily_spend_limit_reads_revision_and_guards_rise(config: Config) -> None:
    respx.get(f"{BASE}/ad_account/spend_limit_windows").mock(
        return_value=httpx.Response(
            200,
            json={"data": [], "revision": 12, "daily_limit": {"amount_micros": 100_000_000}},
        )
    )
    post = respx.post(f"{BASE}/ad_account/daily_spend_limit").mock(
        return_value=httpx.Response(200, json={"spend_limits": {}})
    )
    mcp = _build(config)
    out = await call(mcp, "openai_ads_set_daily_spend_limit", {"amount": 200})
    assert out["refused"] is True
    await call(mcp, "openai_ads_set_daily_spend_limit", {"amount": 90})
    assert _body(post) == {"amount_micros": 90_000_000, "expected_revision": 12}


async def test_deleting_a_limit_needs_delete(config: Config) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/ad_account/spend_limit_windows").mock(
            return_value=httpx.Response(200, json={"data": [], "revision": 3})
        )
        delete = router.post(f"{BASE}/ad_account/daily_spend_limit/delete").mock(
            return_value=httpx.Response(200, json={})
        )
        out = await call(_build(config), "openai_ads_delete_daily_spend_limit", {})
    assert out["required_confirm"] == "DELETE" and delete.call_count == 0


# ---------------------------------------------------------------- audiences and files


@respx.mock
async def test_inline_identifiers_never_reach_the_audit_log(
    config: Config, tmp_audit_log: Path
) -> None:
    route = respx.post(f"{BASE}/custom_audiences/caud_1/add").mock(
        return_value=httpx.Response(200, json={"operation_id": "op_1"})
    )
    await call(
        _build(config),
        "openai_ads_add_audience_members",
        {
            "audience_id": "caud_1",
            "identifiers": ["person@example.com", "other@example.com"],
            "identifier_type": "email",
        },
    )
    assert _body(route)["identifiers"] == ["person@example.com", "other@example.com"]
    assert "Idempotency-Key" in route.calls.last.request.headers
    log = tmp_audit_log.read_text()
    assert "person@example.com" not in log
    assert "<2 identifiers, not logged>" in log


async def test_replace_members_needs_apply(config: Config) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/custom_audiences/caud_1").mock(
            return_value=httpx.Response(200, json={"membership_revision": 4})
        )
        replace = router.post(f"{BASE}/custom_audiences/caud_1/replace").mock(
            return_value=httpx.Response(200, json={})
        )
        out = await call(
            _build(config),
            "openai_ads_replace_audience_members",
            {"audience_id": "caud_1", "file_id": "f", "identifier_type": "email"},
        )
    assert out["required_confirm"] == "APPLY" and replace.call_count == 0


async def test_local_upload_needs_upload_dir(config: Config, tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"\x89PNG")
    message = await call_error(
        _build(config), "openai_ads_upload_image", {"file_path": str(tmp_path / "a.png")}
    )
    assert "OPENAI_ADS_UPLOAD_DIR" in message


async def test_local_upload_stays_inside_upload_dir(config: Config, tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (tmp_path / "secret.png").write_bytes(b"x")
    message = await call_error(
        _build(config, upload_dir=uploads),
        "openai_ads_upload_image",
        {"file_path": "../secret.png"},
    )
    assert "outside OPENAI_ADS_UPLOAD_DIR" in message


@respx.mock
async def test_upload_audience_file_returns_create_fields(config: Config, tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "list.csv").write_text("email\nperson@example.com\n")
    route = respx.post(f"{BASE}/uploads").mock(
        return_value=httpx.Response(200, json={"file_id": "f1"})
    )
    out = await call(
        _build(config, upload_dir=uploads),
        "openai_ads_upload_audience_file",
        {"file_path": "list.csv"},
    )
    assert out["file_id"] == "f1"
    assert out["filename"] == "list.csv" and out["mimetype"] == "text/csv"
    assert out["file_size"] == len("email\nperson@example.com\n")
    assert b"custom_audience" in route.calls.last.request.content


# ---------------------------------------------------------------- secrets returned once


@respx.mock
async def test_conversions_api_key_is_saved_not_shown(config: Config) -> None:
    respx.post(f"{BASE}/conversions/api_keys").mock(
        return_value=httpx.Response(200, json={"name": "srv", "api_key": "capi-value-0987654321"})
    )
    out = await call(_build(config), "openai_ads_create_conversions_api_key", {"name": "srv"})
    assert "capi-value-0987654321" not in json.dumps(out)
    saved = Path(out["secret_saved_to"])
    assert saved.parent == config.secrets_dir / "created"
    assert json.loads(saved.read_text())["api_key"] == "capi-value-0987654321"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(saved).st_mode) == 0o600


@respx.mock
async def test_sftp_password_is_saved_not_shown(config: Config) -> None:
    respx.post(f"{BASE}/feeds/fd_1/sftp_access").mock(
        return_value=httpx.Response(
            200,
            json={"enabled": True, "connection_uri": "sftp://x", "password": "sftp-pass-123456"},
        )
    )
    out = await call(
        _build(config),
        "openai_ads_configure_feed_sftp",
        {"feed_id": "fd_1", "authentication_method": "password"},
    )
    assert "sftp-pass-123456" not in json.dumps(out)
    assert out["connection_uri"] == "sftp://x"


async def test_private_ssh_key_is_refused(config: Config) -> None:
    message = await call_error(
        _build(config),
        "openai_ads_configure_feed_sftp",
        {
            "feed_id": "fd_1",
            "authentication_method": "ssh_key",
            "ssh_public_key": "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
        },
    )
    assert "private key" in message


# ---------------------------------------------------------------- reporting


@respx.mock
async def test_get_insights_encodes_query(config: Config) -> None:
    route = respx.get(f"{BASE}/campaigns/cmpn_1/insights").mock(
        return_value=httpx.Response(200, json={"data": [{"clicks": 3}], "has_more": False})
    )
    await call(
        _build(config),
        "openai_ads_get_insights",
        {
            "scope": "campaign",
            "entity_id": "cmpn_1",
            "start_date": "2026-10-01",
            "end_date": "2026-10-07",
            "fields": ["campaign.clicks", "campaign.spend"],
            "time_granularity": "none",
        },
    )
    params = route.calls.last.request.url.params
    assert params.get_list("fields[]") == ["campaign.clicks", "campaign.spend"]
    assert json.loads(params["time_ranges[]"]) == {
        "type": "date_range",
        "since": "2026-10-01",
        "until": "2026-10-07",
    }
    assert params["time_granularity"] == "none"


@respx.mock
async def test_run_insights_follows_pages(config: Config) -> None:
    route = respx.get(f"{BASE}/ad_account/insights").mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"r": 1}], "has_more": True, "last_id": "p1"}),
            httpx.Response(200, json={"data": [{"r": 2}], "has_more": False, "last_id": "p2"}),
        ]
    )
    out = await call(
        _build(config),
        "openai_ads_run_insights",
        {"start_date": "2026-10-01", "end_date": "2026-10-02", "aggregation_level": "campaign"},
    )
    assert out["rows"] == [{"r": 1}, {"r": 2}]
    assert out["pages"] == 2 and out["truncated"] is False
    assert route.calls[1].request.url.params["after"] == "p1"


@respx.mock
async def test_conversion_insights_uses_account_timezone(config: Config) -> None:
    respx.get(f"{BASE}/ad_account").mock(
        return_value=httpx.Response(200, json={"timezone": "America/New_York"})
    )
    route = respx.post(f"{BASE}/conversions/insights").mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )
    await call(
        _build(config),
        "openai_ads_get_conversion_insights",
        {
            "aggregation_level": "campaign",
            "start_date": "2026-09-01",
            "end_date": "2026-09-07",
            "entity_ids": ["cmpn_123"],
        },
    )
    body = _body(route)
    time_range = json.loads(body["time_ranges"][0])
    # Matches the documented example: Sept 1-7 2026 in New York.
    assert time_range == {"type": "unix_range", "start": "1788235200", "end": "1788840000"}


# ---------------------------------------------------------------- escape hatch and bulk


async def test_raw_write_needs_apply_and_bad_paths_fail(config: Config) -> None:
    mcp = _build(config)
    with respx.mock(assert_all_called=False):
        out = await call(
            mcp, "openai_ads_request", {"method": "POST", "path": "/api_keys", "body": {}}
        )
        assert out["required_confirm"] == "APPLY"
    for path in ["https://evil.example/x", "/../ad_account", "/campaigns?x=1"]:
        message = await call_error(mcp, "openai_ads_read_request", {"method": "GET", "path": path})
        assert "path must be relative" in message or "Leave out" in message


@respx.mock
async def test_raw_read_allows_read_only_post_and_stashes_secrets(config: Config) -> None:
    respx.post(f"{BASE}/feeds/fd_1/products/query").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    respx.post(f"{BASE}/api_keys").mock(
        return_value=httpx.Response(200, json={"api_key": "brand-new-key-abcdef123"})
    )
    mcp = _build(config)
    assert await call(
        mcp, "openai_ads_read_request", {"method": "POST", "path": "/feeds/fd_1/products/query"}
    ) == {"data": []}
    message = await call_error(
        mcp, "openai_ads_read_request", {"method": "POST", "path": "/api_keys"}
    )
    assert "openai_ads_request" in message
    out = await call(
        mcp,
        "openai_ads_request",
        {"method": "POST", "path": "/api_keys", "body": {}, "confirm": "APPLY"},
    )
    assert "brand-new-key-abcdef123" not in json.dumps(out)


@respx.mock
async def test_bulk_validate_only_by_default_and_waits(config: Config, tmp_audit_log: Path) -> None:
    submit = respx.post(f"{BASE}/bulk_mutation_jobs").mock(
        return_value=httpx.Response(202, json={"id": "blk_1", "status": "pending"})
    )
    respx.get(f"{BASE}/bulk_mutation_jobs/blk_1").mock(
        return_value=httpx.Response(200, json={"id": "blk_1", "status": "completed"})
    )
    respx.get(f"{BASE}/bulk_mutation_jobs/blk_1/operations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"operation_id": "a", "status": "validated"}],
                "has_more": False,
                "complete": True,
            },
        )
    )
    ops = [
        {
            "operation_id": "a",
            "type": "campaign.update",
            "target_resource_id": "cmpn_1",
            "input": {"status": "paused"},
        }
    ]
    out = await call(
        _build(config), "openai_ads_submit_bulk_job", {"operations": ops, "poll_seconds": 0}
    )
    assert _body(submit)["validate_only"] is True
    assert out["results"]["summary"] == {"validated": 1}
    assert audit_entries(tmp_audit_log) == []  # validation changes nothing


async def test_applying_a_bulk_job_needs_apply(config: Config) -> None:
    ops = [
        {
            "operation_id": "a",
            "type": "ad.update",
            "target_resource_id": "ad_1",
            "input": {"status": "paused"},
        }
    ]
    with respx.mock(assert_all_called=False) as router:
        submit = router.post(f"{BASE}/bulk_mutation_jobs").mock(
            return_value=httpx.Response(202, json={})
        )
        out = await call(
            _build(config),
            "openai_ads_submit_bulk_job",
            {"operations": ops, "validate_only": False},
        )
    assert out["required_confirm"] == "APPLY" and submit.call_count == 0


async def test_bulk_rejects_malformed_operations(config: Config) -> None:
    message = await call_error(
        _build(config),
        "openai_ads_submit_bulk_job",
        {"operations": [{"operation_id": "a", "type": "campaign.create", "input": {"name": "x"}}]},
    )
    assert "idempotency_key" in message


# ---------------------------------------------------------------- account


async def test_brand_change_and_activation_need_apply(config: Config) -> None:
    mcp = _build(config)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/ad_account").mock(return_value=httpx.Response(200, json={}))
        out = await call(mcp, "openai_ads_update_account_brand", {"brand_name": "Acme"})
        assert out["required_confirm"] == "APPLY"
        out = await call(mcp, "openai_ads_activate_account", {})
        assert out["required_confirm"] == "APPLY"


@pytest.mark.parametrize("dry", [True])
async def test_dry_run_rehearses_guarded_write(
    config: Config, dry: bool, tmp_audit_log: Path
) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/ad_account").mock(return_value=httpx.Response(200, json={}))
        activate = router.post(f"{BASE}/ad_account/activate").mock(return_value=httpx.Response(200))
        out = await call(_build(config, dry_run=dry), "openai_ads_activate_account", {})
    assert out["_dry_run"] is True
    assert out["safety"]["required_confirm"] == "APPLY"
    assert activate.call_count == 0
    assert audit_entries(tmp_audit_log)[-1]["dry_run"] is True
