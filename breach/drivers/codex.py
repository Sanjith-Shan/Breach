"""OpenAI Codex CLI driver.

A second vendor is not a nicety. A pattern that shows up in one agent is a bug
in that agent, and only a pattern that shows up across independently built
agents is a statement about how these systems are currently built. Codex is a
different company, a different model family, a different scaffold, and a
different sandbox, so it is the strongest cheap contrast available.

Two operational notes learned the hard way and worth keeping in the code.
``-m`` is mandatory: the configured default model on the authoring machine is
rejected by ChatGPT-account auth with a 400, so leaving it unset silently
produces zero successful trials. And ``codex exec`` reads stdin when it is
open, so every invocation closes it, otherwise a batch run blocks forever on
the first trial.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from .base import Driver, DriverResult, TrialContext


class CodexDriver(Driver):
    def __init__(self, model: str = "gpt-5.5", label: str | None = None,
                 sandbox: str = "workspace-write", binary: str = "codex"):
        self.model = model
        self.sandbox = sandbox
        self.binary = binary
        self.name = label or f"codex:{model}"
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
        return {"name": self.name, "harness": "codex-cli",
                "cli_version": self._version, "model": self.model,
                "sandbox": self.sandbox,
                "isolation": ["--ephemeral", "--skip-git-repo-check"]}

    supports_gate = True

    def argv(self, prompt: str, workdir, persist: bool = False) -> list[str]:
        return [self.binary, "exec",
                "-m", self.model,
                "-s", self.sandbox,
                "--skip-git-repo-check",
                *([] if persist else ["--ephemeral"]),
                "--json",
                "--color", "never",
                "-C", str(workdir),
                prompt]

    def run(self, ctx: TrialContext) -> DriverResult:
        transcript = ctx.artifact_dir / "transcript.jsonl"
        t0 = time.monotonic()
        try:
            p = subprocess.run(self.argv(ctx.prompt, ctx.workdir,
                                         persist=bool(ctx.extra.get("resumable"))),
                               cwd=ctx.workdir, capture_output=True, text=True,
                               timeout=ctx.timeout_s, env=dict(os.environ),
                               stdin=subprocess.DEVNULL)
            out, err, code, timed_out = p.stdout, p.stderr, p.returncode, False
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err, code, timed_out = "timeout", 124, True
        transcript.write_text(out)

        commands, final_text, turns, thread_id = [], "", 0, None
        for line in out.splitlines():
            if not line.strip().startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "thread.started":
                thread_id = ev.get("thread_id") or (ev.get("thread") or {}).get("id")
            if ev.get("type") == "turn.completed":
                turns += 1
            if ev.get("type") != "item.completed":
                continue
            item = ev.get("item") or {}
            kind = item.get("item_type") or item.get("type")
            if kind == "command_execution" and isinstance(item.get("command"), str):
                commands.append(_strip_shell_wrapper(item["command"]))
            elif kind == "file_change":
                for ch in item.get("changes") or []:
                    commands.append(f"<edit> {ch.get('path')}")
            elif kind == "agent_message":
                final_text = item.get("text") or final_text

        return DriverResult(
            ok=(code == 0) and not timed_out,
            commands=commands,
            session_id=thread_id,
            edited=any(c.startswith("<edit> ") for c in commands),
            num_turns=turns or None,
            error=None if code == 0 else (err or f"exit {code}")[:2000],
            raw={"exit_code": code, "timed_out": timed_out,
                 "wall_s": round(time.monotonic() - t0, 2),
                 "final_text": final_text[:4000],
                 "stderr_tail": (err or "")[-2000:]},
        )


    def follow_up(self, ctx: TrialContext, message: str,
                  session_id: str | None) -> DriverResult | None:
        if not session_id:
            return None
        argv = [self.binary, "exec", "resume", session_id,
                "-m", self.model, "-s", self.sandbox,
                "--skip-git-repo-check", "--json", "--color", "never",
                "-C", str(ctx.workdir), message]
        try:
            p = subprocess.run(argv, cwd=ctx.workdir, capture_output=True, text=True,
                               timeout=ctx.timeout_s, env=dict(os.environ),
                               stdin=subprocess.DEVNULL)
            out, code, timed_out = p.stdout, p.returncode, False
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            code, timed_out = 124, True
        (ctx.artifact_dir / "transcript.followup.jsonl").write_text(out)
        commands, final_text = [], ""
        for line in out.splitlines():
            if not line.strip().startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "item.completed":
                continue
            item = ev.get("item") or {}
            kind = item.get("item_type") or item.get("type")
            if kind == "command_execution" and isinstance(item.get("command"), str):
                commands.append(_strip_shell_wrapper(item["command"]))
            elif kind == "file_change":
                for ch in item.get("changes") or []:
                    commands.append(f"<edit> {ch.get('path')}")
            elif kind == "agent_message":
                final_text = item.get("text") or final_text
        return DriverResult(
            ok=(code == 0) and not timed_out,
            commands=commands, session_id=session_id,
            edited=any(c.startswith("<edit> ") for c in commands),
            error=None if code == 0 else f"exit {code}",
            raw={"exit_code": code, "timed_out": timed_out,
                 "final_text": final_text[:4000]},
        )


def _strip_shell_wrapper(cmd: str) -> str:
    """Codex reports commands wrapped as ``/bin/zsh -lc '<cmd>'``.

    Unwrap so that command matching is comparing like with like across
    drivers. If the shape is unfamiliar, leave it alone rather than guess.
    """
    for prefix in ("/bin/zsh -lc ", "/bin/bash -lc ", "bash -lc ", "zsh -lc "):
        if cmd.startswith(prefix):
            rest = cmd[len(prefix):].strip()
            if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "'\"":
                return rest[1:-1]
            return rest
    return cmd
