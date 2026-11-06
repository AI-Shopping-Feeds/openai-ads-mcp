from __future__ import annotations

from pathlib import Path

import pytest

from openai_ads_mcp.config import load_config

from .conftest import API_KEY


def test_user_env_file_is_read(_isolated_env: Path) -> None:
    _isolated_env.mkdir(parents=True, exist_ok=True)
    (_isolated_env / ".env").write_text(f"OPENAI_ADS_API_KEY={API_KEY}\nOPENAI_ADS_DRY_RUN=true\n")
    config = load_config()
    assert config.api_key == API_KEY
    assert config.dry_run is True


def test_cwd_env_wins_over_user_env(_isolated_env: Path, tmp_path: Path) -> None:
    _isolated_env.mkdir(parents=True, exist_ok=True)
    (_isolated_env / ".env").write_text("OPENAI_ADS_API_KEY=user-level-key-123456\n")
    (tmp_path / ".env").write_text(f"OPENAI_ADS_API_KEY={API_KEY}\n")
    assert load_config().api_key == API_KEY


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_ADS_API_KEY", API_KEY)
    config = load_config()
    assert config.max_increase_pct == 20
    assert config.bulk_threshold == 10
    assert config.audit_retention_days == 30
    assert config.host == "127.0.0.1"
    assert config.allow_remote is False
    assert config.upload_dir is None
    assert config.api_base == "https://api.ads.openai.com/v1"


@pytest.mark.parametrize(
    ("var", "value", "message"),
    [
        ("OPENAI_ADS_PAGE_SIZE", "900", "between 1 and 500"),
        ("OPENAI_ADS_TRANSPORT", "carrier-pigeon", "OPENAI_ADS_TRANSPORT"),
        ("OPENAI_ADS_AD_ACCOUNT_ID", "bad id/../", "adacct_123"),
        ("OPENAI_ADS_MAX_BID", "lots", "must be a number"),
        ("OPENAI_ADS_UPLOAD_DIR", "/no/such/folder/here", "not a folder"),
    ],
)
def test_bad_settings_fail_clearly(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str, message: str
) -> None:
    monkeypatch.setenv("OPENAI_ADS_API_KEY", API_KEY)
    monkeypatch.setenv(var, value)
    with pytest.raises(RuntimeError) as exc:
        load_config()
    assert message in str(exc.value)


def test_key_with_whitespace_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_ADS_API_KEY", "two words")
    with pytest.raises(RuntimeError):
        load_config()
