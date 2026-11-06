"""End-to-end tests through `mcp.Client` against the real server (fake key, mocked HTTP)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import Tool

from openai_ads_mcp import __version__, server, tools
from openai_ads_mcp.client import OpenAIAdsClient
from openai_ads_mcp.config import Config, load_config
from openai_ads_mcp.tool_catalog import ALWAYS_REGISTERED, TOOL_SPECS

from .conftest import API_KEY, BASE

META_TOOLS = [
    "openai_ads_describe_server",
    "openai_ads_health_check",
    "openai_ads_audit_lookup",
    "openai_ads_audit_tail",
    "openai_ads_set_dry_run",
]
EXPECTED_TOOL_COUNT = 76


def _build(config: Config, **changes: Any) -> MCPServer:
    return server.build_server(dataclasses.replace(config, **changes))


async def _tools(mcp: MCPServer) -> list[Tool]:
    async with Client(mcp) as c:
        return (await c.list_tools()).tools


def _group_tool_names(client: OpenAIAdsClient) -> dict[str, list[str]]:
    """Tool names per group, by registering each module on its own server."""
    out: dict[str, list[str]] = {}
    for group in tools.TOOL_GROUPS:
        mcp = MCPServer("probe")
        getattr(tools, group).register(mcp, client)
        out[group] = list(mcp._tool_manager._tools)
    return out


# ---------------------------------------------------------------- metadata


async def test_server_reports_version_and_instructions(config: Config) -> None:
    async with Client(_build(config)) as c:
        assert c.server_info is not None
        assert c.server_info.name == "openai-ads-mcp"
        assert c.server_info.version == __version__
        assert c.instructions
        assert "openai_ads_describe_server" in c.instructions
        assert "dry-run" in c.instructions
        assert "OPENAI_ADS_TOOL_GROUPS" in c.instructions
        assert "identify people" in c.instructions
        assert 4 <= len(c.instructions.splitlines()) <= 8


async def test_every_tool_has_a_catalog_entry(config: Config) -> None:
    listed = await _tools(_build(config))
    assert len(listed) == EXPECTED_TOOL_COUNT
    for tool in listed:
        assert tool.name.startswith("openai_ads_"), tool.name
        assert tool.name in TOOL_SPECS, f"{tool.name} has no tool_catalog entry"
        assert tool.title, tool.name
        assert tool.title[0].isupper(), tool.name
        assert tool.description, tool.name
        a = tool.annotations
        assert a is not None, tool.name
        assert a.read_only_hint is not None, tool.name
        assert a.destructive_hint is not None, tool.name
        assert a.idempotent_hint is not None, tool.name
        assert a.open_world_hint is not None, tool.name
    # The catalog has no stale entries either.
    assert {t.name for t in listed} == set(TOOL_SPECS)


async def test_annotations_are_plausible(config: Config) -> None:
    by_name = {t.name: t.annotations for t in await _tools(_build(config))}
    read_only = {n for n, a in by_name.items() if a and a.read_only_hint}
    destructive = {n for n, a in by_name.items() if a and a.destructive_hint}
    assert not read_only & destructive
    for name in by_name:
        verb = name.removeprefix("openai_ads_").split("_")[0]
        if verb in {
            "list",
            "get",
            "query",
            "search",
            "preview",
            "run",
            "read",
            "describe",
            "audit",
        }:
            assert name in read_only, name
        if verb in {"archive", "delete", "remove", "replace"}:
            assert name in destructive, name
        if (
            verb
            in {
                "create",
                "update",
                "set",
                "add",
                "merge",
                "upload",
                "pause",
                "activate",
                "configure",
                "submit",
                "cancel",
                "resume",
            }
            and name != "openai_ads_set_dry_run"
        ):
            assert name not in read_only, name
    assert "openai_ads_request" in destructive
    assert {"openai_ads_run_insights", "openai_ads_read_request"} <= read_only
    closed = {n for n, a in by_name.items() if a and a.open_world_hint is False}
    assert closed == set(META_TOOLS) - {"openai_ads_health_check"}


async def test_tools_list_order_is_deterministic(config: Config, client: OpenAIAdsClient) -> None:
    first = [t.name for t in await _tools(_build(config))]
    second = [t.name for t in await _tools(_build(config))]
    assert first == second
    per_group = _group_tool_names(client)
    expected = META_TOOLS + [n for g in tools.TOOL_GROUPS for n in per_group[g]]
    assert first == expected


# ---------------------------------------------------------------- filtering


async def test_tool_groups_limit_registration(config: Config, client: OpenAIAdsClient) -> None:
    names = [t.name for t in await _tools(_build(config, tool_groups=["reporting", "campaigns"]))]
    per_group = _group_tool_names(client)
    assert names == META_TOOLS + per_group["campaigns"] + per_group["reporting"]


async def test_read_only_registers_only_read_tools(config: Config) -> None:
    listed = await _tools(_build(config, read_only=True))
    names = {t.name for t in listed}
    assert set(META_TOOLS) <= names
    for tool in listed:
        if tool.name not in ALWAYS_REGISTERED:
            assert tool.annotations and tool.annotations.read_only_hint, tool.name
    assert "openai_ads_update_campaign" not in names
    assert "openai_ads_request" not in names
    assert "openai_ads_list_campaigns" in names
    assert "openai_ads_run_insights" in names
    expected = {n for n, s in TOOL_SPECS.items() if s.annotations.read_only_hint}
    assert names == expected | set(META_TOOLS)


async def test_read_only_combines_with_groups(config: Config) -> None:
    names = {t.name for t in await _tools(_build(config, tool_groups=["status"], read_only=True))}
    assert names == set(META_TOOLS)


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    monkeypatch.setenv("OPENAI_ADS_API_KEY", API_KEY)
    for k, v in values.items():
        monkeypatch.setenv(k, v)


async def test_env_vars_filter_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, OPENAI_ADS_TOOL_GROUPS="account,spend_limits", OPENAI_ADS_READ_ONLY="1")
    names = {t.name for t in await _tools(server.build_server())}
    assert names == set(META_TOOLS) | {
        "openai_ads_get_ad_account",
        "openai_ads_list_ad_accounts",
        "openai_ads_list_account_changes",
        "openai_ads_get_spend_limits",
    }


def test_unknown_group_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, OPENAI_ADS_TOOL_GROUPS="campaigns,nope")
    with pytest.raises(RuntimeError) as exc_info:
        server.build_server()
    message = str(exc_info.value)
    assert "nope" in message
    for group in tools.TOOL_GROUPS:
        assert group in message


def test_load_config_normalises_group_names(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, OPENAI_ADS_TOOL_GROUPS="Reporting, ad-groups")
    assert load_config().tool_groups == ["ad_groups", "reporting"]


# ---------------------------------------------------------------- calls


@respx.mock
async def test_api_error_reaches_the_client(config: Config) -> None:
    respx.get(f"{BASE}/campaigns").mock(
        return_value=httpx.Response(
            403, json={"error": {"message": "Conversion bidding is not enabled", "type": "x"}}
        )
    )
    async with Client(_build(config)) as c:
        result = await c.call_tool("openai_ads_list_campaigns", {})
    assert result.is_error
    text = " ".join(getattr(block, "text", "") for block in result.content)
    assert "OpenAI Ads API error 403" in text
    assert "Conversion bidding is not enabled" in text
    assert API_KEY not in text


async def test_describe_server_reports_settings(config: Config) -> None:
    async with Client(_build(config, tool_groups=["reporting"], read_only=True)) as c:
        result = await c.call_tool("openai_ads_describe_server", {})
    payload = result.structured_content or json.loads(result.content[0].text)
    assert payload["tool_groups"] == ["reporting"]
    assert payload["read_only"] is True
    assert payload["api_base"] == BASE
    assert payload["version"] == __version__
    assert API_KEY not in json.dumps(payload)


@respx.mock
async def test_health_check_summarises_account(config: Config) -> None:
    respx.get(f"{BASE}/ad_account").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "adacct_1",
                "brand_name": "Acme",
                "status": "active",
                "currency_code": "GBP",
                "timezone": "Europe/London",
                "review": {"status": "rejected", "reason": "missing_favicon"},
            },
        )
    )
    async with Client(_build(config)) as c:
        result = await c.call_tool("openai_ads_health_check", {})
    payload = result.structured_content or json.loads(result.content[0].text)
    assert payload["ok"] is True
    assert payload["account"]["currency_code"] == "GBP"
    assert any("missing_favicon" in n for n in payload["notes"])


@respx.mock
async def test_health_check_reports_bad_key(config: Config) -> None:
    respx.get(f"{BASE}/ad_account").mock(return_value=httpx.Response(401, json={"error": "no"}))
    async with Client(_build(config)) as c:
        result = await c.call_tool("openai_ads_health_check", {})
    payload = result.structured_content or json.loads(result.content[0].text)
    assert payload["ok"] is False
    assert "401" in payload["checks"][0]["error"]


async def test_set_dry_run_and_audit_tail(config: Config) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE}/campaigns").mock(return_value=httpx.Response(200, json={}))
        async with Client(_build(config)) as c:
            await c.call_tool("openai_ads_set_dry_run", {"enabled": True})
            created = await c.call_tool(
                "openai_ads_create_campaign",
                {"name": "x campaign", "bidding_type": "clicks", "daily_budget": 5},
            )
            tail = await c.call_tool("openai_ads_audit_tail", {"limit": 5})
            lookup_id = json.loads(created.content[0].text)["audit_id"]
            found = await c.call_tool("openai_ads_audit_lookup", {"audit_id": lookup_id})
    assert json.loads(created.content[0].text)["_dry_run"] is True
    assert json.loads(tail.content[0].text)["count"] == 1
    assert json.loads(found.content[0].text)["found"] is True


def test_version_matches_pyproject() -> None:
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert f'\nversion = "{__version__}"\n' in pyproject


def test_docs_list_every_tool() -> None:
    root = Path(__file__).resolve().parent.parent
    docs = (root / "docs" / "tools.md").read_text(encoding="utf-8")
    missing = [name for name in TOOL_SPECS if f"`{name}`" not in docs]
    assert not missing, f"docs/tools.md is missing {missing}"
    assert f"**{len(TOOL_SPECS)} tools" in docs
