"""sys.modules-level injection of pydoll-derived humanize math into cloakbrowser.

``install()`` registers our ``mouse_async`` and ``keyboard_async`` modules in
sys.modules under the names cloakbrowser expects, BEFORE cloakbrowser is
imported anywhere in the process. Python's import system caches modules in
sys.modules, so subsequent ``from cloakbrowser.human.mouse_async import ...``
calls inside cloakbrowser/human/__init__.py resolve to our versions.

Critical: ``install()`` MUST run before ``import cloakbrowser`` (anywhere).
``hermes_plugin_cloak/__init__.py`` calls it at the very top of the module
load — that's the earliest point we control. If anything else imports
cloakbrowser before our plugin's top-level runs, the original modules are
already cached and our replacement is ignored.

Idempotent: calling install() twice is a no-op. Calling install() AFTER
cloakbrowser has already been imported raises a clear RuntimeError so
misuse is loud rather than silent.
"""
from __future__ import annotations

import logging
import sys
from importlib import import_module

logger = logging.getLogger(__name__)

_INSTALLED = False

_TARGETS = (
    "cloakbrowser.human.mouse_async",
    "cloakbrowser.human.keyboard_async",
)


def install() -> bool:
    """Register our replacements in sys.modules. Return True on first install.

    Idempotent. Raises RuntimeError if cloakbrowser.human is already
    imported (which means our replacement would be ignored).
    """
    global _INSTALLED
    if _INSTALLED:
        return False

    # If cloakbrowser.human is already imported, we're too late.
    if "cloakbrowser.human" in sys.modules:
        raise RuntimeError(
            "hermes_plugin_cloak.humanize.install() called AFTER cloakbrowser.human "
            "was already imported. The pydoll-derived math will NOT be applied. "
            "Ensure 'import hermes_plugin_cloak' runs before anything imports "
            "cloakbrowser."
        )

    # Importing our replacements registers them under THEIR canonical paths
    # (hermes_plugin_cloak.humanize.mouse_async etc.). Then we alias them to
    # cloakbrowser's paths.
    our_mouse = import_module("hermes_plugin_cloak.humanize.mouse_async")
    our_kbd = import_module("hermes_plugin_cloak.humanize.keyboard_async")

    sys.modules["cloakbrowser.human.mouse_async"] = our_mouse
    sys.modules["cloakbrowser.human.keyboard_async"] = our_kbd

    # Mark them so tests / runtime introspection can verify the swap worked.
    our_mouse.__cloak_shim_patched__ = True  # type: ignore[attr-defined]
    our_kbd.__cloak_shim_patched__ = True  # type: ignore[attr-defined]

    _INSTALLED = True
    logger.info(
        "hermes_plugin_cloak.humanize: installed pydoll-derived math into %s",
        ", ".join(_TARGETS),
    )
    return True


def is_installed() -> bool:
    return _INSTALLED


__all__ = ["install", "is_installed"]
