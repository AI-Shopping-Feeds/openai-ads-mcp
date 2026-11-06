from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from openai_ads_mcp.auth import (
    ApiKeyAuth,
    redact,
    register_secret,
    save_key_file,
    stash_secrets,
)
from openai_ads_mcp.config import load_config, read_key_file

POSIX = sys.platform != "win32"


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_redact_hides_registered_secrets_patterns_and_secret_keys() -> None:
    register_secret("my-registered-secret-value")
    text = "key my-registered-secret-value and Bearer abcdefghijklmnop and sk-proj-ABCDEFGHIJKLMNOPQRST"
    out = redact(text)
    assert "my-registered-secret-value" not in out
    assert "abcdefghijklmnop" not in out
    assert "sk-proj-ABCDEFGHIJKLMNOPQRST" not in out
    nested = redact(
        {
            "api_key": "x",
            "password": "p",
            "signing_secret": "s",
            "name": "ok",
            "list": ["Bearer zzzzzzzzzz"],
        }
    )
    assert nested["api_key"] == "***"
    assert nested["password"] == "***"
    assert nested["signing_secret"] == "***"
    assert nested["name"] == "ok"
    assert nested["list"] == ["***"]


def test_auth_headers_and_partner_account() -> None:
    auth = ApiKeyAuth("key-for-headers-123456")
    assert auth.headers() == {"Authorization": "Bearer key-for-headers-123456"}
    assert auth.account_label.endswith("3456")
    partner = ApiKeyAuth("key-for-headers-123456", "adacct_9")
    assert partner.headers()["OpenAI-Ad-Account"] == "adacct_9"
    assert partner.headers("adacct_override")["OpenAI-Ad-Account"] == "adacct_override"
    with pytest.raises(RuntimeError):
        ApiKeyAuth("")


def test_save_key_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "openai-ads-key.json"
    save_key_file(path, "saved-key-abcdefgh", "adacct_1")
    assert read_key_file(path) == ("saved-key-abcdefgh", "adacct_1")
    if POSIX:
        assert _mode(path) == 0o600
        assert _mode(path.parent) == 0o700


def test_load_config_reads_key_file_from_home(_isolated_env: Path) -> None:
    save_key_file(_isolated_env / "secrets" / "openai-ads-key.json", "file-key-abcdefgh")
    config = load_config()
    assert config.api_key == "file-key-abcdefgh"
    assert config.audit_log == _isolated_env / "logs" / "audit.jsonl"
    assert config.secrets_dir == _isolated_env / "secrets"


def test_env_key_wins_over_file(_isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_key_file(_isolated_env / "secrets" / "openai-ads-key.json", "file-key-abcdefgh")
    monkeypatch.setenv("OPENAI_ADS_API_KEY", "env-key-abcdefgh")
    assert load_config().api_key == "env-key-abcdefgh"


def test_missing_key_explains_what_to_do() -> None:
    with pytest.raises(RuntimeError) as exc:
        load_config()
    message = str(exc.value)
    assert "openai-ads-mcp auth" in message
    assert "not an OpenAI platform API key" in message
    assert load_config(require_key=False).api_key is None


def test_stash_secrets_moves_credentials_to_a_private_file(tmp_path: Path) -> None:
    payload = {"name": "Server key", "api_key": "capi-secret-value-123456"}
    out = stash_secrets(payload, kind="conversions-api-key", label="Server key", directory=tmp_path)
    assert "capi-secret-value-123456" not in json.dumps(out)
    saved = Path(out["secret_saved_to"])
    assert json.loads(saved.read_text())["api_key"] == "capi-secret-value-123456"
    if POSIX:
        assert _mode(saved) == 0o600
    # The value is now redacted anywhere else it shows up.
    assert "capi-secret-value-123456" not in redact("echo capi-secret-value-123456")
    # Payloads without secrets pass through unchanged.
    assert stash_secrets({"id": "x"}, kind="k", directory=tmp_path) == {"id": "x"}
