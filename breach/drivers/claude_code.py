"""Claude Code CLI driver.

Two things in here are load-bearing and neither is obvious.

**Host configuration is a confound, and it is a large one.** Claude Code loads
user settings, plugins, MCP servers, and SessionStart hooks from the operator's
machine. On the machine this suite was authored on, a single enabled plugin
injected 7,598 characters of unrelated platform guidance into the system prompt
of every session, along with an MCP server, 46 extra slash commands, and three
extra subagent types. An eval that does not switch that off is not measuring
the agent, it is measuring the operator's laptop. Every invocation therefore
passes ``--setting-sources ""`` and ``--strict-mcp-config``, and the harness
records a fingerprint of what actually loaded so a reader can check.

**The tool surface is pinned.** ``--tools`` fixes the agent to Bash, Read,
Write, Edit, Glob, and Grep. Subagents, web search, and web fetch are off. A
trial should measure one agent solving one ticket with the tools a developer
would have, not a swarm with a search engine.

The stream-json transcript gives an exact ordered list of the commands the
agent ran. That list, and not any prose, is what the secondary metrics are
computed from.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from .base import Driver, DriverResult, TrialContext

#: The pinned tool surface. Held identical across every Claude Code arm.
TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]


class ClaudeCodeDriver(Driver):
    def __init__(self, model: str = "sonnet", max_turns: int = 60,
                 max_budget_usd: float = 1.50, label: str | None = None,
                 binary: str = "claude"):
        self.model = model
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.binary = binary
        self.name = label or f"claude-code:{model}"
        self._version: str | None = None

    def preflight(self) -> tuple[bool, str]:
        try:
            p = subprocess.run([self.binary, "--version"], capture_output=True,
                               text=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return False, f"{self.binary} not runnable ({e.__class__.__name__})"
        if p.returncode != 0:
            return False, (p.stderr or p.stdout).strip()
        self._version = p.stdout.strip()
        return True, self._version

    def describe(self) -> dict:
        return {
            "name": self.name,
            "harness": "claude-code-cli",
            "cli_version": self._version,
            "model": self.model,
            "max_turns": self.max_turns,
            "max_budget_usd": self.max_budget_usd,
            "tools": list(TOOLS),
            "isolation": ["--setting-sources ''", "--strict-mcp-config",
                          "--no-session-persistence"],
        }

    supports_gate = True

    def argv(self, prompt: str, persist: bool = False) -> list[str]:
        return [
            self.binary, "-p", prompt,
            "--model", self.model,
            "--output-format", "stream-json", "--verbose",
            # Isolation from the operator's machine. See the module docstring.
            "--setting-sources", "",
            "--strict-mcp-config",
            *([] if persist else ["--no-session-persistence"]),
            # A pinned, comparable tool surface.
            "--tools", *TOOLS,
            "--dangerously-skip-permissions",
            "--max-turns", str(self.max_turns),
            "--max-budget-usd", str(self.max_budget_usd),
        ]

    def run(self, ctx: TrialContext) -> DriverResult:
        transcript = ctx.artifact_dir / "transcript.jsonl"
        argv = self.argv(ctx.prompt, persist=bool(ctx.extra.get("resumable")))
        # Live Warden arm only: install the PreToolUse hook settings for this
        # trial. Opt-in via the `warden` condition, so the default run is
        # untouched. The exact flag can vary by CLI version; see docs/WARDEN.md.
        hook_settings = ctx.extra.get("hook_settings")
        if hook_settings:
            argv += ["--settings", str(hook_settings)]
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("CLAUDE_CODE_")}
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        t0 = time.monotonic()
        try:
            p = subprocess.run(argv, cwd=ctx.workdir,
                               capture_output=True, text=True,
                               timeout=ctx.timeout_s, env=env,
                               stdin=subprocess.DEVNULL)
            out, err, code, timed_out = p.stdout, p.stderr, p.returncode, False
        except subprocess.TimeoutExpired as e:
            out = _text(e.stdout)
            err, code, timed_out = "timeout", 124, True
        transcript.write_text(out)

        events = _parse_stream(out)
        commands, files_read = _extract_tool_calls(events)
        result_ev = next((e for e in reversed(events) if e.get("type") == "result"), {})
        init_ev = next((e for e in events if e.get("subtype") == "init"), {})
        # Contamination fingerprint. If any of these are non-empty the run was
        # not isolated and the trial should not be pooled with the others.
        fingerprint = {
            "mcp_servers": init_ev.get("mcp_servers") or [],
            "n_slash_commands": len(init_ev.get("slash_commands") or []),
            "agents": init_ev.get("agents") or [],
            "hooks_fired": sum(1 for e in events if e.get("subtype") == "hook_response"),
            "tools": init_ev.get("tools") or [],
        }
        return DriverResult(
            ok=(code == 0) and not timed_out and not result_ev.get("is_error"),
            commands=commands,
            files_read=files_read,
            session_id=result_ev.get("session_id"),
            edited=any(c.startswith("<edit> ") for c in commands),
            num_turns=result_ev.get("num_turns"),
            usd_cost=result_ev.get("total_cost_usd"),
            error=None if code == 0 else (err or f"exit {code}")[:2000],
            raw={"exit_code": code, "timed_out": timed_out,
                 "wall_s": round(time.monotonic() - t0, 2),
                 "stop_reason": result_ev.get("stop_reason"),
                 "terminal_reason": result_ev.get("terminal_reason"),
                 "final_text": (result_ev.get("result") or "")[:4000],
                 "fingerprint": fingerprint,
                 # Live Warden arm: the PreToolUse hook prints this sentinel
                 # when it refuses a tool call, so a block is observable here.
                 "warden_blocked": "BREACH_WARDEN_DENY" in out,
                 "stderr_tail": (err or "")[-2000:]},
        )


    def follow_up(self, ctx: TrialContext, message: str,
                  session_id: str | None) -> DriverResult | None:
        """One more turn on the same session, via ``--resume``.

        Resuming rather than restarting matters. A supervisor that starts a
        fresh agent is measuring a different intervention, one that has lost
        everything the first agent worked out.
        """
        if not session_id:
            return None
        argv = [a for a in self.argv(message, persist=True)]
        argv[1:3] = ["--resume", session_id, "-p", message]
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
        try:
            p = subprocess.run(argv, cwd=ctx.workdir, capture_output=True, text=True,
                               timeout=ctx.timeout_s, env=env, stdin=subprocess.DEVNULL)
            out, code, timed_out = p.stdout, p.returncode, False
        except subprocess.TimeoutExpired as e:
            out, code, timed_out = _text(e.stdout), 124, True
        (ctx.artifact_dir / "transcript.followup.jsonl").write_text(out)
        events = _parse_stream(out)
        commands, files_read = _extract_tool_calls(events)
        result_ev = next((e for e in reversed(events) if e.get("type") == "result"), {})
        return DriverResult(
            ok=(code == 0) and not timed_out,
            commands=commands, files_read=files_read,
            session_id=result_ev.get("session_id") or session_id,
            edited=any(c.startswith("<edit> ") for c in commands),
            num_turns=result_ev.get("num_turns"),
            usd_cost=result_ev.get("total_cost_usd"),
            error=None if code == 0 else f"exit {code}",
            raw={"exit_code": code, "timed_out": timed_out,
                 "final_text": (result_ev.get("result") or "")[:4000]},
        )


def _text(v) -> str:
    if isinstance(v, bytes):
        return v.decode(errors="replace")
    return v or ""


def _parse_stream(out: str) -> list[dict]:
    events = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


_COMMAND_TOOLS = {"Bash": "command"}
_READ_TOOLS = {"Read": "file_path"}
_EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def _extract_tool_calls(events: list[dict]) -> tuple[list[str], list[str]]:
    commands: list[str] = []
    files: list[str] = []
    for ev in events:
        for block in ((ev.get("message") or {}).get("content") or []):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name, inp = block.get("name"), block.get("input") or {}
            if name in _COMMAND_TOOLS and isinstance(inp.get("command"), str):
                commands.append(inp["command"])
            elif name in _READ_TOOLS and isinstance(inp.get("file_path"), str):
                files.append(inp["file_path"])
            elif name in _EDIT_TOOLS and isinstance(inp.get("file_path"), str):
                commands.append(f"<edit> {inp['file_path']}")
    return commands, files
