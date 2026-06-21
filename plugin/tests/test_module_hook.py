"""Verify the sys.modules hook correctly aliases cloakbrowser submodules
to our pydoll-derived replacements.

These tests do NOT require cloakbrowser to be installed — they check the
mapping by inspecting sys.modules entries after install() and confirming
the __cloak_shim_patched__ marker is present.
"""
from __future__ import annotations

import sys

import pytest

from hermes_plugin_cloak import humanize


def test_install_idempotent():
    """Calling install() a second time is a no-op (returns False)."""
    # First call may have already happened at package import time.
    first = humanize.install()
    second = humanize.install()
    # At least one of them must have returned False (idempotent guard).
    assert not (first and second)


def test_mouse_async_aliased_in_sys_modules():
    humanize.install()
    aliased = sys.modules.get("cloakbrowser.human.mouse_async")
    assert aliased is not None, "cloakbrowser.human.mouse_async should be aliased"
    # Our module sets __cloak_shim_patched__ = True after install().
    assert getattr(aliased, "__cloak_shim_patched__", False) is True
    # And it must expose the cloakbrowser-compatible surface.
    for name in ("AsyncRawMouse", "async_human_move", "async_human_click", "async_human_idle"):
        assert hasattr(aliased, name), f"missing required export: {name}"


def test_keyboard_async_aliased_in_sys_modules():
    humanize.install()
    aliased = sys.modules.get("cloakbrowser.human.keyboard_async")
    assert aliased is not None
    assert getattr(aliased, "__cloak_shim_patched__", False) is True
    for name in ("AsyncRawKeyboard", "async_human_type"):
        assert hasattr(aliased, name)


def test_install_after_cloakbrowser_imported_raises(monkeypatch):
    """If something imports cloakbrowser.human BEFORE us, install() must raise."""
    # Reset the _INSTALLED flag and pretend cloakbrowser.human got imported.
    monkeypatch.setattr(humanize, "_INSTALLED", False)
    monkeypatch.setitem(sys.modules, "cloakbrowser.human", object())  # any sentinel

    with pytest.raises(RuntimeError, match="already imported"):
        humanize.install()
