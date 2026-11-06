from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

from openai_ads_mcp.audit import DAY, AuditLog

from .conftest import audit_entries


def _write(log: AuditLog, ts: float) -> str:
    audit_id = log.record(op="x", resource="/r", method="POST", url="u")
    entries = audit_entries(log.path)
    entries[-1]["ts"] = ts
    import json

    log.path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return audit_id


def test_record_redacts_and_is_owner_only(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "logs" / "audit.jsonl")
    audit_id = log.record(
        op="create_conversions_api_key",
        resource="/conversions/api_keys",
        method="POST",
        url="u",
        response_body={"name": "k", "api_key": "plain-capi-key-value"},
    )
    entry = log.find(audit_id)
    assert entry is not None
    assert entry["response_body"]["api_key"] == "***"
    assert "plain-capi-key-value" not in log.path.read_text()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(log.path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(log.path.parent).st_mode) == 0o700


def test_prune_drops_old_entries(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl", retention_days=30)
    now = time.time()
    _write(log, now - 40 * DAY)
    keep = log.record(op="y", resource="/r", method="POST", url="u")
    assert log.prune(now=now) == 1
    assert [e["audit_id"] for e in log.entries()] == [keep]


def test_retention_zero_keeps_everything(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl", retention_days=0)
    _write(log, 0)
    assert log.prune() == 0
    assert len(log.entries()) == 1


def test_tail_and_purge(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    ids = [log.record(op=str(i), resource="/r", method="POST", url="u") for i in range(5)]
    assert [e["audit_id"] for e in log.tail(2)] == ids[-2:]
    assert log.tail(0) == []
    assert log.purge() is True
    assert log.entries() == []
    assert log.purge() is False
