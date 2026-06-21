"""Sentinel returned when automated captcha solving fails.

The agent's prompt instructs it: if a captcha-solving tool returns this
exact string, call ``kanban_block(reason=...)`` immediately to ask the
human to handle it on the VNC dashboard.

We use a plain string (not an Exception) because Hermes tools commonly
return strings to the LLM, and the LLM can pattern-match on the value
in its decision logic without try/except wrappers.
"""
from __future__ import annotations

MANUAL_INTERVENTION_REQUIRED: str = "MANUAL_INTERVENTION_REQUIRED"
