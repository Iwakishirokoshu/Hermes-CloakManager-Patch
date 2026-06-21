#!/usr/bin/env python3
"""
NotLetters API CLI (httpx-based).

Reads API key from (in order):
    $NOTLETTERS_API_KEY   $NL_KEY   /etc/notletters.env   ~/nl_key.txt

Commands:
    balance                            — account info / balance / rate-limit
    letters EMAIL:PASS [--search Q]    — list latest letters
    codes EMAIL:PASS [EMAIL2:PASS2..]  — list letters that contain numeric codes
    wait EMAIL:PASS --sender X         — block until OTP arrives, print {"code": "..."}
    watch EMAIL:PASS [...]             — live-monitor new letters (Ctrl+C stops)
    change-password --payload '{...}'  — proxy POST /v1/letters/password (stub)
    buy --payload '{...}'              — proxy POST /v1/emails/buy (stub)

All commands accept --json for machine output.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import httpx
except ImportError:
    print(
        "error: httpx not installed. Run from Hermes venv:\n"
        "  /usr/local/lib/hermes-agent/venv/bin/python " + __file__,
        file=sys.stderr,
    )
    sys.exit(2)

API_BASE = "https://api.notletters.com"
DEFAULT_TIMEOUT = 30.0


# ---------- key / config ------------------------------------------------------

def _read_envfile(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def get_api_key() -> str:
    for var in ("NOTLETTERS_API_KEY", "NL_KEY"):
        if os.environ.get(var):
            return os.environ[var]
    for path in (Path("/etc/notletters.env"), Path.home() / "nl_key.txt"):
        if path.exists():
            content = _read_envfile(path) if path.suffix == ".env" or path.name.endswith(".env") else {}
            for var in ("NOTLETTERS_API_KEY", "NL_KEY"):
                if content.get(var):
                    return content[var]
            try:
                txt = path.read_text(encoding="utf-8").strip()
                if txt and "\n" not in txt and "=" not in txt:
                    return txt
            except OSError:
                pass
    print(
        "error: NotLetters API key not found. Put it in /etc/notletters.env "
        "as NOTLETTERS_API_KEY=... (chmod 600), or set $NOTLETTERS_API_KEY.",
        file=sys.stderr,
    )
    sys.exit(2)


def mask_key(k: str) -> str:
    if not k or len(k) < 6:
        return "***"
    return f"{k[:2]}...{k[-2:]}"


# ---------- http --------------------------------------------------------------

class NotLettersClient:
    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT, debug: bool = False):
        self._client = httpx.Client(
            base_url=API_BASE,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "hermes-skill-notletters/1.0",
            },
        )
        self._debug = debug

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _request(self, method: str, path: str, json_body: Any = None) -> dict:
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            try:
                r = self._client.request(method, path, json=json_body)
            except httpx.HTTPError as exc:
                if attempt == max_attempts:
                    return {"error": "http_error", "detail": str(exc)}
                time.sleep(2 ** attempt)
                continue

            if r.status_code == 429 and attempt < max_attempts:
                time.sleep(2 ** attempt)
                continue

            try:
                payload = r.json()
            except Exception:
                payload = {"error": "non_json", "status": r.status_code, "body": r.text[:500]}

            if r.status_code >= 400:
                payload.setdefault("error", f"http_{r.status_code}")
                payload.setdefault("status", r.status_code)
            return payload

        return {"error": "exhausted_retries"}

    # endpoints ---
    def me(self) -> dict:
        return self._request("GET", "/v1/me")

    def letters(self, email: str, password: str, search: str | None = None, star: bool = False) -> dict:
        body: dict[str, Any] = {"email": email, "password": password}
        if search or star:
            body["filters"] = {"search": search or "", "star": bool(star)}
        return self._request("POST", "/v1/letters", body)

    def change_password(self, payload: dict) -> dict:
        return self._request("POST", "/v1/letters/password", payload)

    def buy(self, payload: dict) -> dict:
        return self._request("POST", "/v1/emails/buy", payload)


# ---------- helpers -----------------------------------------------------------

CODE_CONTEXT_WORDS = (
    "code", "verification", "confirm", "verify", "OTP",
    "код", "подтверд", "подтверж",
)


def find_codes(text: str, digits: int = 6) -> dict:
    """Return {"primary": "123456", "candidates": [...]} or {"primary": None}."""
    if not text:
        return {"primary": None, "candidates": []}

    if digits == 0:
        candidates = re.findall(r"\b\d{4,8}\b", text)
    else:
        pattern = rf"\b\d{{{digits}}}\b"
        candidates = re.findall(pattern, text)

    if not candidates:
        return {"primary": None, "candidates": []}

    # prefer a code that appears within ±60 chars of a context word
    boosted: list[str] = []
    rest: list[str] = []
    low = text.lower()
    for c in candidates:
        idx = text.find(c)
        slice_ = low[max(0, idx - 60): idx + len(c) + 60]
        if any(w.lower() in slice_ for w in CODE_CONTEXT_WORDS):
            boosted.append(c)
        else:
            rest.append(c)
    ordered = boosted + rest
    return {"primary": ordered[0], "candidates": ordered}


def parse_creds_arg(arg: str) -> tuple[str, str]:
    if ":" not in arg:
        raise ValueError(f"need email:password, got '{arg}'")
    e, p = arg.split(":", 1)
    return e.strip(), p


def fmt_date(unix_ts: float | int | None) -> str:
    if not unix_ts:
        return "?"
    try:
        return datetime.fromtimestamp(int(unix_ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "?"


# ---------- commands ----------------------------------------------------------

def cmd_balance(args, cli: NotLettersClient) -> int:
    res = cli.me()
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0 if "error" not in res else 1
    if "error" in res:
        print(f"error: {res}", file=sys.stderr)
        return 1
    d = res.get("data", res) or {}
    print(f"Account   : {d.get('username') or '?'}")
    print(f"Balance   : {d.get('balance', '?')} руб")
    print(f"Rate lim. : {d.get('rate_limit', '?')} req/s")
    return 0


def cmd_letters(args, cli: NotLettersClient) -> int:
    email, password = parse_creds_arg(args.creds)
    res = cli.letters(email, password, search=args.search)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0 if "error" not in res else 1
    if "error" in res:
        print(f"error: {res}", file=sys.stderr)
        return 1
    letters = (res.get("data") or {}).get("letters") or []
    if not letters:
        print(f"({email}) no letters")
        return 0
    print(f"=== {email} : {len(letters)} letter(s) ===")
    for l in letters:
        text = (l.get("letter") or {}).get("text") or ""
        codes = find_codes(text, digits=0)["candidates"][:3]
        line = f"[{fmt_date(l.get('date'))}]  {l.get('sender_name','?')} <{l.get('sender','?')}>  | {l.get('subject','')}"
        print(line)
        if codes:
            print(f"    codes: {', '.join(codes)}")
        if args.full:
            print("    " + (text[:600].replace("\n", "\n    ")))
    return 0


def cmd_codes(args, cli: NotLettersClient) -> int:
    out: list[dict] = []
    rc = 0
    for cred in args.creds:
        email, password = parse_creds_arg(cred)
        res = cli.letters(email, password)
        if "error" in res:
            out.append({"email": email, "error": res})
            rc = 1
            continue
        for l in (res.get("data") or {}).get("letters") or []:
            text = (l.get("letter") or {}).get("text") or ""
            cands = find_codes(text, digits=0)["candidates"]
            if cands:
                out.append({
                    "email": email,
                    "subject": l.get("subject"),
                    "sender": l.get("sender"),
                    "date": fmt_date(l.get("date")),
                    "codes": cands,
                })
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return rc
    if not out:
        print("no codes found")
        return rc
    for entry in out:
        if "error" in entry:
            print(f"[{entry['email']}] error: {entry['error']}")
        else:
            print(f"[{entry['date']}] {entry['email']} | {entry['subject']}")
            print(f"    codes: {', '.join(entry['codes'])}")
    return rc


def cmd_wait(args, cli: NotLettersClient) -> int:
    email, password = parse_creds_arg(args.creds)
    sender_filter = (args.sender or "").lower().strip()
    digits = args.digits
    timeout = args.timeout
    poll = max(args.poll, 2)
    start_ts = time.time()
    cutoff = start_ts if args.since_now else 0

    elapsed = 0.0
    while elapsed < timeout:
        res = cli.letters(email, password)
        if "error" not in res:
            for l in (res.get("data") or {}).get("letters") or []:
                date = l.get("date") or 0
                if date < cutoff:
                    continue
                sender_blob = (l.get("sender", "") + " " + l.get("sender_name", "")).lower()
                if sender_filter and sender_filter not in sender_blob:
                    continue
                text = (l.get("letter") or {}).get("text") or ""
                codes = find_codes(text, digits=digits)
                if codes["primary"]:
                    out = {
                        "code": codes["primary"],
                        "candidates": codes["candidates"],
                        "letter_id": l.get("id"),
                        "subject": l.get("subject"),
                        "sender": l.get("sender"),
                        "date": fmt_date(date),
                    }
                    print(json.dumps(out, ensure_ascii=False))
                    return 0
        elapsed = time.time() - start_ts
        if elapsed + poll < timeout:
            time.sleep(poll)
        else:
            break

    if args.json:
        print(json.dumps({"code": None, "error": "timeout", "elapsed": int(elapsed)}))
    else:
        print(f"timeout after {int(elapsed)}s — no matching letter for sender='{sender_filter}'", file=sys.stderr)
    return 1


def cmd_watch(args, cli: NotLettersClient) -> int:
    creds = [parse_creds_arg(c) for c in args.creds]
    seen: dict[str, set[str]] = {e: set() for e, _ in creds}
    print(f"watching {len(creds)} mailbox(es), interval={args.interval}s. Ctrl+C stops.")
    # baseline
    for email, password in creds:
        res = cli.letters(email, password)
        if "error" in res:
            print(f"  [{email}] init error: {res}")
            continue
        for l in (res.get("data") or {}).get("letters") or []:
            seen[email].add(l.get("id") or "")
    print(f"baseline: {sum(len(v) for v in seen.values())} letter(s). watching for new...")
    try:
        while True:
            time.sleep(args.interval)
            for email, password in creds:
                res = cli.letters(email, password)
                if "error" in res:
                    continue
                for l in (res.get("data") or {}).get("letters") or []:
                    lid = l.get("id") or ""
                    if lid and lid not in seen[email]:
                        seen[email].add(lid)
                        text = (l.get("letter") or {}).get("text") or ""
                        codes = find_codes(text, digits=0)["candidates"]
                        ts = fmt_date(l.get("date"))
                        suffix = f"  | codes: {', '.join(codes)}" if codes else ""
                        print(f"[{ts}] NEW {email} | {l.get('subject','')}{suffix}")
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_change_password(args, cli: NotLettersClient) -> int:
    payload = json.loads(args.payload)
    res = cli.change_password(payload)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if "error" not in res else 1


def cmd_buy(args, cli: NotLettersClient) -> int:
    payload = json.loads(args.payload)
    res = cli.buy(payload)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if "error" not in res else 1


# ---------- argparse ----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # Common flags available both top-level (before cmd) and on each sub-command.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--debug", action="store_true", help="enable httpx debug logging")
    common.add_argument("--json", action="store_true", help="machine-readable output")

    p = argparse.ArgumentParser(prog="notletters", description="NotLetters API CLI", parents=[common])
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("balance", help="account info", parents=[common])

    p_l = sp.add_parser("letters", help="list letters of one mailbox", parents=[common])
    p_l.add_argument("creds", help="email:password")
    p_l.add_argument("--search", help="full-text search filter")
    p_l.add_argument("--full", action="store_true", help="show body preview (600 chars)")

    p_c = sp.add_parser("codes", help="extract codes from letters", parents=[common])
    p_c.add_argument("creds", nargs="+", help="email:password [...]")

    p_w = sp.add_parser("wait", help="block until OTP arrives", parents=[common])
    p_w.add_argument("creds", help="email:password")
    p_w.add_argument("--sender", default="", help="substring of sender to filter on")
    p_w.add_argument("--digits", type=int, default=6, help="exact code length (0=any 4-8)")
    p_w.add_argument("--timeout", type=int, default=180, help="max wait seconds")
    p_w.add_argument("--poll", type=int, default=5, help="polling interval seconds")
    p_w.add_argument("--since-now", action="store_true",
                     help="ignore letters older than command start (recommended)")

    p_wa = sp.add_parser("watch", help="live-monitor new letters", parents=[common])
    p_wa.add_argument("creds", nargs="+", help="email:password [...]")
    p_wa.add_argument("--interval", type=int, default=5, help="poll seconds")

    p_pw = sp.add_parser("change-password", help="proxy POST /v1/letters/password", parents=[common])
    p_pw.add_argument("--payload", required=True, help="JSON string")

    p_b = sp.add_parser("buy", help="proxy POST /v1/emails/buy", parents=[common])
    p_b.add_argument("--payload", required=True, help="JSON string")

    return p


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    api_key = get_api_key()
    if args.debug:
        print(f"[debug] API key: {mask_key(api_key)}", file=sys.stderr)

    with NotLettersClient(api_key, debug=args.debug) as cli:
        dispatch = {
            "balance": cmd_balance,
            "letters": cmd_letters,
            "codes": cmd_codes,
            "wait": cmd_wait,
            "watch": cmd_watch,
            "change-password": cmd_change_password,
            "buy": cmd_buy,
        }
        return dispatch[args.cmd](args, cli)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
