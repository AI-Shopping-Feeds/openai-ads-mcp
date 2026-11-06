"""Shared fixtures for the test suite.

All tests run fully offline: HTTP is mocked with respx and only fake keys are used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from openai_ads_mcp.audit import AuditLog
from openai_ads_mcp.auth import ApiKeyAuth
from openai_ads_mcp.client import OpenAIAdsClient
from openai_ads_mcp.config import Config

BASE = "https://api.ads.openai.com/v1"
API_KEY = "test-ads-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep tests away from the developer's ~/.openai-ads-mcp and real env vars."""
    for var in list(os.environ):
        if var.startswith("OPENAI_ADS_"):
            monkeypatch.delenv(var, raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("OPENAI_ADS_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("openai_ads_mcp.client.time.sleep", lambda s: slept.append(s))
    return slept


@pytest.fixture
def tmp_audit_log(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "audit.jsonl"


@pytest.fixture
def config(tmp_audit_log: Path, tmp_path: Path) -> Config:
    return Config(
        api_key=API_KEY,
        audit_log=tmp_audit_log,
        key_file=tmp_path / "secrets" / "key.json",
        secrets_dir=tmp_path / "secrets",
        page_size=100,
        timeout=5.0,
        dry_run=False,
    )


@pytest.fixture
def audit(tmp_audit_log: Path) -> AuditLog:
    return AuditLog(tmp_audit_log)


@pytest.fixture
def client(config: Config, audit: AuditLog):
    c = OpenAIAdsClient(config, ApiKeyAuth(API_KEY), audit)
    yield c
    c.close()


def audit_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
