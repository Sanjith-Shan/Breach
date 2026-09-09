#!/usr/bin/env python3
"""Claude Code PreToolUse hook: the live arm of the Warden integration.

Installed by ``breach.warden.install_warden_settings`` as::

    BREACH_PROJECT_ID=<id> python3 <repo>/tools/warden_gate/hook.py

Claude Code invokes this once before each tool call, feeding it a JSON object
on stdin (at minimum ``tool_name`` and ``tool_input``) and reading a hook
response from stdout. This script maps that tool call to a Warden action
(``{"tool": "bash", "command": ...}`` for a shell command, or
``{"tool": "str_replace_editor", "paths": [...]}`` for a file edit), asks
``breach.warden.WardenClient.check`` what Warden thinks, and — only if Warden
would deny the action or require an approval nobody is here to give in a
headless run — emits a PreToolUse "deny" decision *and* the literal token
``BREACH_WARDEN_DENY``, which is what ``breach``'s Claude Code driver greps
stdout for to recognise a live block.

This is a measurement harness, not a security product: a broken or
unreachable Warden must never silently masquerade as a working defense, so
every failure mode here (missing env var, bad JSON, transport error,
whatever) fails OPEN — exit 0, no hook output, tool call proceeds normally.
``WardenClient.check`` itself already fails open (returns an "allow" verdict)
when Warden is unavailable, so the only extra care needed here is around
parsing the hook input.
"""

from __future__ import annotations

import json
import os
import sys

DENY_TOKEN = "BREACH_WARDEN_DENY"


def _load_warden_client():
    """Import breach.warden, adding the repo root to sys.path if needed."""
    here = os.path.dirname(os.path.abspath(__file__))
    # tools/warden_gate/hook.py -> tools/warden_gate -> tools -> <repo root>
    repo_root = os.path.dirname(os.path.dirname(here))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from breach import warden  # noqa: PLC0415 (imported lazily, on purpose)
    return warden


def _action_from_hook_input(payload: dict) -> dict:
    """Map a Claude Code PreToolUse hook payload to a Warden action dict."""
    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}

    if tool_name.lower() == "bash":
        return {"tool": "bash", "command": tool_input.get("command", "")}

    file_path = tool_input.get("file_path")
    if file_path:
        return {"tool": "str_replace_editor", "paths": [file_path]}

    # Anything else (a custom/MCP tool, a search, etc.): pass through what we
    # have so Warden's classifier can still look at it, without guessing at a
    # shape we don't recognise.
    return {"tool": tool_name or "unknown", "raw_input": tool_input}


def _deny_response(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}

        project_id = os.environ.get("BREACH_PROJECT_ID")
        if not project_id:
            return 0  # not wired up for this run; fail open

        warden = _load_warden_client()
        client = warden.WardenClient.from_env()
        if not client.available:
            return 0  # no Warden transport configured; fail open

        action = _action_from_hook_input(payload)
        verdict = client.check(project_id, action)

        if verdict.blocks:
            reason = f"Warden {verdict.verdict} [{verdict.risk_class}]: {verdict.reason}".strip()
            print(json.dumps(_deny_response(reason)))
            # The driver detects a live block by finding this literal token
            # in the CLI's stdout, independent of the JSON above.
            print(DENY_TOKEN)
        return 0
    except Exception:
        # Any failure here (bad stdin, import error, transport hiccup that
        # somehow escaped WardenClient.check, etc.) must not block the agent
        # or crash the hook process -- fail open, always exit 0.
        return 0


if __name__ == "__main__":
    sys.exit(main())
