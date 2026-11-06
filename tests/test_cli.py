from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import click
import httpx
import pytest
import respx
from click.testing import CliRunner

from openai_ads_mcp import cli as cli_mod
from openai_ads_mcp.cli import check_bind, cli

from .conftest import API_KEY, BASE


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_allowed(host: str) -> None:
    check_bind("streamable-http", host, allow_remote=False)


def test_stdio_never_binds() -> None:
    check_bind("stdio", "0.0.0.0", allow_remote=False)


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_non_loopback_http_is_refused_unless_allowed(transport: str) -> None:
    with pytest.raises(click.ClickException) as exc:
        check_bind(transport, "0.0.0.0", allow_remote=False)
    assert "OPENAI_ADS_ALLOW_REMOTE" in exc.value.message
    check_bind(transport, "0.0.0.0", allow_remote=True)


def test_run_refuses_remote_bind_before_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_ADS_API_KEY", API_KEY)
    started: list[bool] = []
    monkeypatch.setattr(cli_mod, "build_server", lambda config: started.append(True))
    result = CliRunner().invoke(
        cli, ["--transport", "streamable-http", "--host", "0.0.0.0", "run"], obj={}
    )
    assert result.exit_code != 0
    assert "Refusing to bind" in result.output
    assert not started


def test_env_allow_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_ADS_API_KEY", API_KEY)
    monkeypatch.setenv("OPENAI_ADS_ALLOW_REMOTE", "true")

    class FakeServer:
        def run(self, *args: object, **kwargs: object) -> None:
            self.args = args

    fake = FakeServer()
    monkeypatch.setattr(cli_mod, "build_server", lambda config: fake)
    result = CliRunner().invoke(
        cli, ["--transport", "streamable-http", "--host", "0.0.0.0", "run"], obj={}
    )
    assert result.exit_code == 0, result.output
    assert fake.args == ("streamable-http",)


@respx.mock
def test_auth_verifies_and_saves_key(_isolated_env: Path) -> None:
    route = respx.get(f"{BASE}/ad_account").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "adacct_1",
                "brand_name": "Acme",
                "currency_code": "USD",
                "timezone": "UTC",
            },
        )
    )
    result = CliRunner().invoke(cli, ["auth", "--key-stdin"], input=f"{API_KEY}\n", obj={})
    assert result.exit_code == 0, result.output
    assert API_KEY not in result.output
    assert "adacct_1" in result.output
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {API_KEY}"
    key_file = _isolated_env / "secrets" / "openai-ads-key.json"
    assert json.loads(key_file.read_text())["api_key"] == API_KEY
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600


@respx.mock
def test_auth_with_bad_key_saves_nothing(_isolated_env: Path) -> None:
    respx.get(f"{BASE}/ad_account").mock(return_value=httpx.Response(401, json={"error": "bad"}))
    result = CliRunner().invoke(cli, ["auth", "--key-stdin"], input="bad-key-12345678\n", obj={})
    assert result.exit_code != 0
    assert "Nothing was saved" in result.output
    assert not (_isolated_env / "secrets" / "openai-ads-key.json").exists()


def test_purge_deletes_local_data(_isolated_env: Path) -> None:
    files = [
        _isolated_env / "logs" / "audit.jsonl",
        _isolated_env / "secrets" / "openai-ads-key.json",
        _isolated_env / "secrets" / "created" / "conversions-api-key-x.json",
    ]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
    result = CliRunner().invoke(cli, ["purge", "--yes"], obj={})
    assert result.exit_code == 0, result.output
    assert "only copy" in result.output
    assert not any(f.exists() for f in files)


def test_purge_keep_secrets(_isolated_env: Path) -> None:
    audit = _isolated_env / "logs" / "audit.jsonl"
    key = _isolated_env / "secrets" / "openai-ads-key.json"
    for f in (audit, key):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
    result = CliRunner().invoke(cli, ["purge", "--yes", "--keep-secrets"], obj={})
    assert result.exit_code == 0
    assert not audit.exists() and key.exists()


def test_describe_never_prints_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_ADS_API_KEY", API_KEY)
    result = CliRunner().invoke(cli, ["describe"], obj={})
    assert result.exit_code == 0, result.output
    assert API_KEY not in result.output
    info = json.loads(result.output)
    assert info["api_key"].startswith("set")
    assert info["api_base"] == BASE
