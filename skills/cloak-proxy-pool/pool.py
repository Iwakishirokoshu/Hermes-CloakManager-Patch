#!/usr/bin/env python3
"""
cloak-proxy-pool — minimal persistent rotation for Cloak profiles.

Storage: ~/.hermes/cloak/proxies.json  (atomic write via temp+rename)

CLI:
    python3 pool.py load           # parse stdin, merge new proxies
    python3 pool.py next <profile> # atomically claim next free proxy
    python3 pool.py release <profile>
    python3 pool.py status         # total/free/used
    python3 pool.py list           # full dump

All operations are atomic and safe to run from parallel processes
(uses fcntl lockf on POSIX; no-op on Windows).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # POSIX only
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

POOL_DIR = Path(os.environ.get("CLOAK_POOL_DIR", str(Path.home() / ".hermes" / "cloak")))
POOL_FILE = POOL_DIR / "proxies.json"
LOCK_FILE = POOL_DIR / "proxies.lock"


# ---------- IO ----------------------------------------------------------------

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _locked():
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    if not _HAS_FCNTL:
        yield
        return
    with open(LOCK_FILE, "a+") as fp:
        fcntl.lockf(fp.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.lockf(fp.fileno(), fcntl.LOCK_UN)


def _load() -> list[dict]:
    if not POOL_FILE.exists():
        return []
    try:
        with open(POOL_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, list):
            return []
        return data
    except (OSError, json.JSONDecodeError):
        return []


def _save(data: list[dict]) -> None:
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".proxies.", suffix=".tmp", dir=str(POOL_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        os.replace(tmp_path, POOL_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------- Parsing -----------------------------------------------------------

_RE_HAS_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
_RE_USERPASS_AT = re.compile(r"^(?P<u>[^:@\s]+):(?P<p>[^@\s]+)@(?P<h>[^:\s]+):(?P<port>\d+)$")
_RE_HOST_PORT_USERPASS = re.compile(r"^(?P<h>[^:\s]+):(?P<port>\d+):(?P<u>[^:\s]+):(?P<p>[^:\s]+)$")
_RE_HOST_PORT = re.compile(r"^(?P<h>[^:\s]+):(?P<port>\d+)$")


def parse_proxy_line(line: str) -> str | None:
    """Convert a single user-pasted line into a normalized URL.

    Returns None for blanks/comments/garbage.
    """
    s = line.strip()
    if not s or s.startswith("#"):
        return None

    if _RE_HAS_SCHEME.match(s):
        return s

    m = _RE_USERPASS_AT.match(s)
    if m:
        return f"http://{m['u']}:{m['p']}@{m['h']}:{m['port']}"

    m = _RE_HOST_PORT_USERPASS.match(s)
    if m:
        return f"http://{m['u']}:{m['p']}@{m['h']}:{m['port']}"

    m = _RE_HOST_PORT.match(s)
    if m:
        return f"http://{m['h']}:{m['port']}"

    return None


# ---------- Operations --------------------------------------------------------

def cmd_load(argv: list[str]) -> int:
    if sys.stdin.isatty() and not argv:
        print("error: load expects proxies on stdin (one per line)", file=sys.stderr)
        return 2

    raw = sys.stdin.read()
    parsed: list[str] = []
    for line in raw.splitlines():
        url = parse_proxy_line(line)
        if url:
            parsed.append(url)

    with _locked():
        data = _load()
        existing = {item.get("url") for item in data}
        added = 0
        for url in parsed:
            if url in existing:
                continue
            data.append({"url": url, "assigned_to": None, "used_at": None})
            existing.add(url)
            added += 1
        _save(data)

    print(json.dumps({"added": added, "total": len(data), "skipped_duplicates": len(parsed) - added}))
    return 0


def cmd_next(argv: list[str]) -> int:
    if not argv:
        print("error: usage: next <profile_name>", file=sys.stderr)
        return 2
    profile = argv[0]

    with _locked():
        data = _load()
        for idx, item in enumerate(data):
            if item.get("assigned_to"):
                continue
            item["assigned_to"] = profile
            item["used_at"] = _now_iso()
            _save(data)
            print(json.dumps({"url": item["url"], "index": idx, "profile": profile}))
            return 0

    print(json.dumps({"error": "pool_exhausted", "url": None, "profile": profile}), file=sys.stderr)
    return 1


def cmd_release(argv: list[str]) -> int:
    if not argv:
        print("error: usage: release <profile_name>", file=sys.stderr)
        return 2
    profile = argv[0]

    with _locked():
        data = _load()
        released = 0
        for item in data:
            if item.get("assigned_to") == profile:
                item["assigned_to"] = None
                item["used_at"] = None
                released += 1
        _save(data)

    print(json.dumps({"released": released, "profile": profile}))
    return 0


def cmd_status(argv: list[str]) -> int:
    with _locked():
        data = _load()
    used = sum(1 for it in data if it.get("assigned_to"))
    free = len(data) - used
    print(json.dumps({"total": len(data), "used": used, "free": free}))
    return 0


def cmd_list(argv: list[str]) -> int:
    with _locked():
        data = _load()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


# ---------- Main --------------------------------------------------------------

COMMANDS = {
    "load": cmd_load,
    "next": cmd_next,
    "release": cmd_release,
    "status": cmd_status,
    "list": cmd_list,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    fn = COMMANDS.get(cmd)
    if fn is None:
        print(f"error: unknown command '{cmd}'. valid: {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    return fn(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
