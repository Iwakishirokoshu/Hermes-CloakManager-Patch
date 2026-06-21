"""Tests for the module-level browser_tool guard.

The plugin patches ``tools.browser_tool.browser_navigate`` (full replace)
and wraps 9 other browser_* tools with a guard that refuses to fall back
to a local Chromium when ``CLOAK_MANAGER_URL`` is set but
``BROWSER_CDP_URL`` is empty.

We mock ``tools.browser_tool`` and ``model_tools`` in ``sys.modules`` so
the plugin can be imported without a real Hermes install.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from typing import Any, Dict

import pytest


GUARDED_TOOLS = (
    "browser_snapshot",
    "browser_console",
    "browser_back",
    "browser_get_images",
    "browser_vision",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_scroll",
)


@pytest.fixture
def fake_browser_tool(monkeypatch):
    """Inject a fake ``tools.browser_tool`` module before plugin import."""
    pkg = types.ModuleType("tools")
    pkg.__path__ = []
    bt = types.ModuleType("tools.browser_tool")

    sentinel_returns: Dict[str, Any] = {}

    def _make_native(name):
        def native(*a, **kw):
            return sentinel_returns.get(name, f"native:{name}")
        native.__name__ = name
        native.__module__ = "tools.browser_tool"
        return native

    for t in (*GUARDED_TOOLS, "browser_navigate"):
        setattr(bt, t, _make_native(t))

    monkeypatch.setitem(sys.modules, "tools", pkg)
    monkeypatch.setitem(sys.modules, "tools.browser_tool", bt)

    model_tools = types.ModuleType("model_tools")
    def _run_async(coro):
        return coro
    model_tools._run_async = _run_async
    monkeypatch.setitem(sys.modules, "model_tools", model_tools)

    for mod in list(sys.modules):
        if mod.startswith("hermes_plugin_cloak"):
            del sys.modules[mod]

    return bt, sentinel_returns


def _import_and_register(monkeypatch):
    import hermes_plugin_cloak as p
    importlib.reload(p)

    class FakeCtx:
        def __init__(self):
            self.tools = []
            self.hooks = []

        def register_tool(self, **kw):
            self.tools.append(kw["name"])

        def register_hook(self, name, cb):
            self.hooks.append(name)

    ctx = FakeCtx()
    p.register(ctx)
    return p, ctx


def test_version_bumped():
    import hermes_plugin_cloak as p
    importlib.reload(p)
    assert p.__version__ == "0.1.1"


def test_all_guarded_tools_wrapped(fake_browser_tool, monkeypatch):
    bt, _ = fake_browser_tool
    monkeypatch.setenv("CLOAK_MANAGER_URL", "http://127.0.0.1:8080")
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    p, ctx = _import_and_register(monkeypatch)

    assert "cloak_create_profile" in ctx.tools
    assert "pre_tool_call" in ctx.hooks

    for tool in GUARDED_TOOLS:
        fn = getattr(bt, tool)
        assert getattr(fn, "__wrapped_by_cloak__", False), (
            f"{tool} should be wrapped by the cloak guard"
        )

    navigate = bt.browser_navigate
    assert navigate.__module__ == "hermes_plugin_cloak", (
        "browser_navigate must be fully replaced, not just guarded"
    )


def test_guard_refuses_without_cdp(fake_browser_tool, monkeypatch):
    bt, _ = fake_browser_tool
    monkeypatch.setenv("CLOAK_MANAGER_URL", "http://127.0.0.1:8080")
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    _import_and_register(monkeypatch)

    result = bt.browser_snapshot()
    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed["error"] == "no_active_cloak_profile"
    assert parsed["tool"] == "browser_snapshot"
    assert parsed["guard"] == "hermes-plugin-cloak"
    assert "cloak_set_active" in parsed["hint"]


def test_guard_passes_through_with_cdp(fake_browser_tool, monkeypatch):
    bt, sentinel = fake_browser_tool
    monkeypatch.setenv("CLOAK_MANAGER_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("BROWSER_CDP_URL", "ws://127.0.0.1:8081/cdp")
    sentinel["browser_snapshot"] = "native-snapshot-output"

    _import_and_register(monkeypatch)

    result = bt.browser_snapshot()
    assert result == "native-snapshot-output", (
        "with BROWSER_CDP_URL set, guard must pass through to native"
    )


def test_guard_passes_through_without_cloak_manager(fake_browser_tool, monkeypatch):
    bt, sentinel = fake_browser_tool
    monkeypatch.delenv("CLOAK_MANAGER_URL", raising=False)
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    sentinel["browser_click"] = "native-click-output"

    _import_and_register(monkeypatch)

    result = bt.browser_click("e1")
    assert result == "native-click-output", (
        "when Cloak is not configured at all, guard must not block native"
    )


def test_guard_blocks_input_tools(fake_browser_tool, monkeypatch):
    bt, _ = fake_browser_tool
    monkeypatch.setenv("CLOAK_MANAGER_URL", "http://127.0.0.1:8080")
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    _import_and_register(monkeypatch)

    for tool_name in ("browser_click", "browser_type", "browser_press", "browser_scroll"):
        fn = getattr(bt, tool_name)
        try:
            raw = fn("e1") if tool_name in ("browser_click", "browser_press", "browser_scroll") else fn("e1", "txt")
        except TypeError:
            raw = fn()
        parsed = json.loads(raw)
        assert parsed["error"] == "no_active_cloak_profile", (
            f"{tool_name}: guard should refuse without CDP, got {raw}"
        )
        assert parsed["tool"] == tool_name
