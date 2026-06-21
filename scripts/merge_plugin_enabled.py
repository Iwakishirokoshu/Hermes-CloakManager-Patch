#!/usr/bin/env python3
"""Idempotently add a plugin name to plugins.enabled in a Hermes config.yaml.

Usage: merge_plugin_enabled.py /path/to/config.yaml cloak

Preserves comments where possible (uses ruamel.yaml if available, else
falls back to PyYAML which strips comments — acceptable for first-time
edit). Creates the file with a minimal template if missing.
"""
from __future__ import annotations
import sys
from pathlib import Path

def main() -> int:
    if len(sys.argv) != 3:
        print("usage: merge_plugin_enabled.py <config.yaml> <plugin>", file=sys.stderr)
        return 2
    cfg_path = Path(sys.argv[1])
    plugin = sys.argv[2]

    try:
        from ruamel.yaml import YAML  # type: ignore
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)
        loader = lambda fp: yaml.load(fp)
        dumper = lambda data, fp: yaml.dump(data, fp)
        backend = "ruamel"
    except ImportError:
        import yaml as pyyaml  # type: ignore
        loader = lambda fp: pyyaml.safe_load(fp) or {}
        dumper = lambda data, fp: pyyaml.safe_dump(data, fp, sort_keys=False)
        backend = "pyyaml"

    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as fp:
            data = loader(fp) or {}
    else:
        data = {}

    if not isinstance(data, dict):
        print(f"ERROR: {cfg_path} top-level is not a mapping (got {type(data).__name__})", file=sys.stderr)
        return 1

    plugins = data.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        print(f"ERROR: plugins is not a mapping", file=sys.stderr)
        return 1
    enabled = plugins.setdefault("enabled", [])
    if not isinstance(enabled, list):
        print(f"ERROR: plugins.enabled is not a list", file=sys.stderr)
        return 1

    if plugin in enabled:
        print(f"already enabled (backend={backend})")
        return 0

    enabled.append(plugin)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as fp:
        dumper(data, fp)
    print(f"added plugin '{plugin}' to {cfg_path} (backend={backend})")
    return 0

if __name__ == "__main__":
    sys.exit(main())