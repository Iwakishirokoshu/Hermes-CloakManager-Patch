"""Tests for ManagerClient against a mocked HTTP backend."""
from __future__ import annotations

import os

import pytest

# pytest-httpx provides an httpx_mock fixture.
pytest.importorskip("pytest_httpx")

from hermes_plugin_cloak.manager_client import ManagerClient, ManagerError


BASE = "http://127.0.0.1:41833"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CLOAK_MANAGER_URL", raising=False)
    monkeypatch.delenv("CLOAK_AUTH_TOKEN", raising=False)
    yield


async def test_status_no_auth_required(httpx_mock):
    httpx_mock.add_response(
        method="GET", url=f"{BASE}/api/status",
        json={"running_count": 2, "binary_version": "test"},
    )
    async with ManagerClient(base_url=BASE) as mgr:
        data = await mgr.status()
    assert data["running_count"] == 2


async def test_bearer_header_when_token_set(httpx_mock):
    httpx_mock.add_response(
        method="GET", url=f"{BASE}/api/profiles",
        json=[{"id": "x", "name": "y"}],
    )
    async with ManagerClient(base_url=BASE, auth_token="abc123") as mgr:
        await mgr.list_profiles()
    req = httpx_mock.get_requests()[0]
    assert req.headers["authorization"] == "Bearer abc123"


async def test_create_profile_requires_name():
    async with ManagerClient(base_url=BASE) as mgr:
        with pytest.raises(ValueError, match="name"):
            await mgr.create_profile(humanize=True)


async def test_create_profile_posts_full_body(httpx_mock):
    httpx_mock.add_response(
        method="POST", url=f"{BASE}/api/profiles",
        status_code=201,
        json={"id": "uuid-x", "name": "scout-x", "humanize": True},
    )
    async with ManagerClient(base_url=BASE) as mgr:
        prof = await mgr.create_profile(name="scout-x", humanize=True, human_preset="default")
    assert prof["id"] == "uuid-x"

    posted = httpx_mock.get_requests()[0].read().decode()
    assert "scout-x" in posted
    assert "human_preset" in posted


async def test_find_by_name_returns_none(httpx_mock):
    httpx_mock.add_response(
        method="GET", url=f"{BASE}/api/profiles",
        json=[{"id": "u1", "name": "alpha"}, {"id": "u2", "name": "beta"}],
    )
    async with ManagerClient(base_url=BASE) as mgr:
        found = await mgr.find_profile_by_name("gamma")
    assert found is None


async def test_find_by_name_returns_profile(httpx_mock):
    httpx_mock.add_response(
        method="GET", url=f"{BASE}/api/profiles",
        json=[{"id": "u1", "name": "alpha"}, {"id": "u2", "name": "beta"}],
    )
    async with ManagerClient(base_url=BASE) as mgr:
        found = await mgr.find_profile_by_name("beta")
    assert found is not None
    assert found["id"] == "u2"


async def test_launch_returns_cdp_url(httpx_mock):
    httpx_mock.add_response(
        method="POST", url=f"{BASE}/api/profiles/abc/launch",
        json={
            "profile_id": "abc",
            "status": "running",
            "vnc_ws_port": 6100,
            "display": ":100",
            "cdp_url": "/api/profiles/abc/cdp",
        },
    )
    async with ManagerClient(base_url=BASE) as mgr:
        resp = await mgr.launch("abc")
    assert resp["cdp_url"] == "/api/profiles/abc/cdp"
    assert mgr.absolute_cdp_url(resp["cdp_url"]) == f"{BASE}/api/profiles/abc/cdp"


async def test_error_response_raises_manager_error(httpx_mock):
    httpx_mock.add_response(
        method="GET", url=f"{BASE}/api/profiles/missing",
        status_code=404,
        text="profile not found",
    )
    async with ManagerClient(base_url=BASE) as mgr:
        with pytest.raises(ManagerError) as exc_info:
            await mgr.get_profile("missing")
    assert exc_info.value.status_code == 404


async def test_absolute_cdp_url_preserves_full_url():
    async with ManagerClient(base_url=BASE) as mgr:
        already = "https://foo.bar/api/profiles/x/cdp"
        assert mgr.absolute_cdp_url(already) == already

async def test_bind_browser_cdp_env_rewrites_via_proxy(httpx_mock, monkeypatch):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/json/version",
        json={"webSocketDebuggerUrl": "ws://127.0.0.1:8080/api/profiles/abc/cdp"},
    )
    monkeypatch.setenv("CLOAK_CDP_PROXY_BASE", "http://127.0.0.1:8081")
    async with ManagerClient(base_url=BASE, auth_token="tok") as mgr:
        ws = await mgr.bind_browser_cdp_env("/api/profiles/abc/cdp")
    assert ws == "ws://127.0.0.1:8081/api/profiles/abc/cdp"
    assert os.environ["BROWSER_CDP_URL"] == ws
    assert os.environ["CLOAK_CDP_HTTP_URL"] == "http://127.0.0.1:8081/api/profiles/abc/cdp"
