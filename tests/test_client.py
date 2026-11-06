from __future__ import annotations

import dataclasses
from pathlib import Path

import httpx
import pytest
import respx

from openai_ads_mcp import client as client_mod
from openai_ads_mcp.auth import ApiKeyAuth
from openai_ads_mcp.client import (
    OpenAIAdsAPIError,
    OpenAIAdsClient,
    RateLimiter,
    check_path,
    encode_params,
    path_id,
)

from .conftest import API_KEY, BASE, audit_entries


@respx.mock
def test_sends_bearer_key_and_accept(client: OpenAIAdsClient) -> None:
    route = respx.get(f"{BASE}/ad_account").mock(return_value=httpx.Response(200, json={"id": "a"}))
    assert client.request("GET", "/ad_account") == {"id": "a"}
    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {API_KEY}"
    assert req.headers["Accept"] == "application/json"
    assert "OpenAI-Ad-Account" not in req.headers
    assert req.headers["User-Agent"].startswith("openai-ads-mcp/")


@respx.mock
def test_partner_account_header(client: OpenAIAdsClient) -> None:
    client.auth = ApiKeyAuth(API_KEY, "adacct_7")
    route = respx.get(f"{BASE}/campaigns").mock(return_value=httpx.Response(200, json={}))
    client.request("GET", "/campaigns")
    assert route.calls.last.request.headers["OpenAI-Ad-Account"] == "adacct_7"


def test_encode_params_uses_bracket_arrays_and_json() -> None:
    out = encode_params(
        {
            "include": ["serving_issues"],
            "fields[]": ["a", "b"],
            "time_ranges": [{"type": "date_range", "since": "2026-01-01"}],
            "flag": True,
            "skip": None,
            "limit": 5,
        }
    )
    assert ("include[]", "serving_issues") in out
    assert ("fields[]", "a") in out and ("fields[]", "b") in out
    assert ("time_ranges[]", '{"type":"date_range","since":"2026-01-01"}') in out
    assert ("flag", "true") in out
    assert ("limit", "5") in out
    assert all(k != "skip" for k, _ in out)


@pytest.mark.parametrize(
    "bad",
    ["campaigns", "/../x", "https://evil.example/x", "/a?b=1", "/a b", "//evil", "/v1/campaigns"],
)
def test_check_path_rejects_unsafe_paths(bad: str) -> None:
    with pytest.raises(ValueError):
        check_path(bad)


def test_path_id_blocks_injection() -> None:
    assert path_id("cmpn_123") == "cmpn_123"
    for bad in ["cmpn_1/archive", "..", "a?b", "", "x y"]:
        with pytest.raises(ValueError):
            path_id(bad)


@respx.mock
def test_retries_429_with_retry_after(client: OpenAIAdsClient, _no_sleep: list[float]) -> None:
    route = respx.get(f"{BASE}/campaigns").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}, json={}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    assert client.request("GET", "/campaigns") == {"data": []}
    assert route.call_count == 2
    assert _no_sleep and _no_sleep[0] >= 7


@respx.mock
def test_get_retries_server_errors(client: OpenAIAdsClient) -> None:
    route = respx.get(f"{BASE}/campaigns").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    assert client.request("GET", "/campaigns") == {"ok": 1}
    assert route.call_count == 2


@respx.mock
def test_unkeyed_create_is_not_retried_after_server_error(
    client: OpenAIAdsClient, tmp_audit_log: Path
) -> None:
    route = respx.post(f"{BASE}/conversions/pixels").mock(return_value=httpx.Response(503, json={}))
    with pytest.raises(OpenAIAdsAPIError) as exc:
        client.request("POST", "/conversions/pixels", json_body={"name": "x"})
    assert route.call_count == 1
    assert "may have been saved" in str(exc.value)
    assert audit_entries(tmp_audit_log)[-1]["error"] == "HTTP 503"


@respx.mock
def test_keyed_create_is_retried_with_same_key(client: OpenAIAdsClient) -> None:
    route = respx.post(f"{BASE}/campaigns").mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json={"id": "cmpn_1"})]
    )
    out = client.request("POST", "/campaigns", json_body={"name": "x"}, idempotency_key="k-1")
    assert out["id"] == "cmpn_1"
    keys = [c.request.headers["Idempotency-Key"] for c in route.calls]
    assert keys == ["k-1", "k-1"]


@respx.mock
def test_write_is_audited_with_before_snapshot(
    client: OpenAIAdsClient, tmp_audit_log: Path
) -> None:
    respx.post(f"{BASE}/campaigns/cmpn_1").mock(
        return_value=httpx.Response(200, json={"id": "cmpn_1", "name": "new"})
    )
    out = client.request(
        "POST",
        "/campaigns/cmpn_1",
        json_body={"name": "new"},
        op="update_campaign",
        before={"name": "old"},
        idempotent=True,
    )
    entry = audit_entries(tmp_audit_log)[-1]
    assert out["audit_id"] == entry["audit_id"]
    assert entry["op"] == "update_campaign"
    assert entry["before"] == {"name": "old"}
    assert entry["request_body"] == {"name": "new"}
    assert API_KEY not in tmp_audit_log.read_text()


def test_dry_run_sends_nothing(client: OpenAIAdsClient, tmp_audit_log: Path) -> None:
    client.config = dataclasses.replace(client.config, dry_run=True)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{BASE}/campaigns").mock(return_value=httpx.Response(200))
        out = client.request("POST", "/campaigns", json_body={"name": "x"}, op="create_campaign")
    assert out["_dry_run"] is True
    assert route.call_count == 0
    entry = audit_entries(tmp_audit_log)[-1]
    assert entry["dry_run"] is True
    assert entry["request_body"] == {"name": "x"}


@respx.mock
def test_read_only_post_skips_audit_and_dry_run(
    client: OpenAIAdsClient, tmp_audit_log: Path
) -> None:
    client.config = dataclasses.replace(client.config, dry_run=True)
    respx.post(f"{BASE}/conversions/insights").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    assert client.request("POST", "/conversions/insights", json_body={}, read_only=True) == {
        "data": []
    }
    assert audit_entries(tmp_audit_log) == []


@respx.mock
def test_error_messages_are_redacted(client: OpenAIAdsClient) -> None:
    respx.get(f"{BASE}/ad_account").mock(
        return_value=httpx.Response(401, json={"error": {"message": f"bad key {API_KEY}"}})
    )
    with pytest.raises(OpenAIAdsAPIError) as exc:
        client.request("GET", "/ad_account")
    assert API_KEY not in str(exc.value)
    assert exc.value.status == 401
    assert "Ads Manager" in str(exc.value)


@respx.mock
def test_paginate_follows_cursor(client: OpenAIAdsClient) -> None:
    route = respx.get(f"{BASE}/campaigns").mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"id": "a"}], "has_more": True, "last_id": "a"}),
            httpx.Response(200, json={"data": [{"id": "b"}], "has_more": False, "last_id": "b"}),
        ]
    )
    items, truncated, cursor = client.paginate("/campaigns", max_results=10)
    assert [i["id"] for i in items] == ["a", "b"]
    assert not truncated and cursor is None
    assert route.calls[1].request.url.params["after"] == "a"


@respx.mock
def test_paginate_truncates(client: OpenAIAdsClient) -> None:
    respx.get(f"{BASE}/campaigns").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "a"}, {"id": "b"}], "has_more": True, "last_id": "b"}
        )
    )
    items, truncated, cursor = client.paginate("/campaigns", max_results=2)
    assert len(items) == 2 and truncated and cursor == "b"


@respx.mock
def test_response_size_cap(client: OpenAIAdsClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "MAX_RESPONSE_BYTES", 10)
    respx.get(f"{BASE}/campaigns").mock(return_value=httpx.Response(200, content=b"x" * 100))
    with pytest.raises(OpenAIAdsAPIError) as exc:
        client.request("GET", "/campaigns")
    assert "cap" in str(exc.value)


def test_rate_limiter_waits_when_full() -> None:
    waits: list[float] = []
    limiter = RateLimiter(2, 60.0, sleep=lambda s: (waits.append(s), limiter._calls.popleft()))
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    assert waits and waits[0] > 0


@respx.mock
def test_multipart_upload(client: OpenAIAdsClient, tmp_audit_log: Path) -> None:
    route = respx.post(f"{BASE}/upload").mock(
        return_value=httpx.Response(200, json={"file_id": "f"})
    )
    client.request(
        "POST",
        "/upload",
        files={"file": ("a.png", b"\x89PNG", "image/png")},
        form={"purpose": "account_favicon"},
        audit_body={"file": "a.png", "bytes": 4},
    )
    req = route.calls.last.request
    assert req.headers["Content-Type"].startswith("multipart/form-data")
    assert b"account_favicon" in req.content
    assert audit_entries(tmp_audit_log)[-1]["request_body"] == {"file": "a.png", "bytes": 4}
