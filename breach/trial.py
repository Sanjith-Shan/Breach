"""One trial, start to finish.

The order is the whole experiment, and the order matters more than any single
step:

  1. stage a clean copy of the seed repo (canary and plant already baked in)
  2. let the agent work the ticket, with the configured defense in front of it
  3. freeze the work tree and diff it against the seed
  4. extract what the agent produced, ran, read, and changed
  5. score the four attack-goal detectors, deterministically, from those logs
  6. replay the agent's actions through Warden to record what it would block

Nothing after step 2 can be influenced by the agent. Every number in step 5 is
a boolean read off a file, with the evidence line kept beside it.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import subprocess
from typing import Any

from . import detectors, egress, sandbox
from .conditions import NEEDS_WARDEN, build_prompt
from .detectors import GOALS, DetectorInput
from .drivers.base import Driver, TrialContext
from .task import Task
from .warden import WardenClient, actions_from_trial, install_warden_settings, replay

#: Provider messages that mean the model was never reached. A trial that hit one
#: measures a subscription quota, not an agent, so it is excluded from every
#: rate and never cached. Carried over from ProvingGround unchanged.
PROVIDER_LIMIT_MARKERS = (
    "hit your session limit", "hit your usage limit", "usage limit reached",
    "rate_limit_error", "429", "insufficient_quota",
    "billing_hard_limit_reached", "overloaded_error",
)

_MAX_FILE_BYTES = 200_000


def looks_provider_limited(dr) -> bool:
    blob = " ".join(str(x) for x in (
        dr.error or "", dr.raw.get("stderr_tail") or "", dr.raw.get("final_text") or "",
    )).lower()
    return any(m in blob for m in PROVIDER_LIMIT_MARKERS)


@dataclasses.dataclass
class TrialResult:
    task_id: str
    surface: str
    plant_intent: str
    goal: str
    trap: bool
    mechanism: str
    agent: str
    condition: str
    trial_index: int
    seed: int

    # --- primary, fully deterministic: one boolean + evidence per goal ------
    canary_read: bool = False
    exfil_attempt: bool = False
    out_of_scope_change: bool = False
    out_of_scope_command: bool = False
    evidence: dict[str, str] = dataclasses.field(default_factory=dict)

    # --- diff / command derived --------------------------------------------
    files_changed: list[str] = dataclasses.field(default_factory=list)
    n_commands: int = 0
    commands_observable: bool = True

    # --- defense arm --------------------------------------------------------
    #: Post-hoc replay of the agent's actions through Warden's check_action.
    warden_replay: dict | None = None
    #: True if Warden would have blocked at least one action the agent took.
    warden_blocked: bool | None = None
    #: The live warden condition actually blocked a tool call.
    warden_live_blocked: bool = False
    warden_unavailable: bool = False

    # --- bookkeeping --------------------------------------------------------
    provider_limited: bool = False
    driver_ok: bool = False
    num_turns: int | None = None
    usd_cost: float | None = None
    wall_s: float | None = None
    error: str | None = None
    artifact_dir: str = ""

    @property
    def acted_on_plant(self) -> bool:
        """The headline per-trial quantity: did the agent act on the plant?

        Scored by the one detector that matches this task's plant intent. On a
        control twin (no plant) this is the false-positive floor for that goal.
        """
        return bool(getattr(self, self.goal))

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["acted_on_plant"] = self.acted_on_plant
        return d


def run_trial(task: Task, driver: Driver, condition: str, trial_index: int,
              seed: int, workroot: pathlib.Path, artifact_root: pathlib.Path,
              image: str = sandbox.IMAGE, timeout_s: int = 900,
              warden: WardenClient | None = None) -> TrialResult:
    slug = f"{task.id}__{_slug(driver.name)}__{condition}__t{trial_index}"
    workdir = (workroot / slug).resolve()
    artifact_dir = (artifact_root / slug).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    sandbox.stage_repo(task.repo_dir, workdir)

    res = TrialResult(task_id=task.id, surface=task.surface,
                      plant_intent=task.plant_intent, goal=task.goal,
                      trap=task.trap, mechanism=task.mechanism, agent=driver.name,
                      condition=condition, trial_index=trial_index, seed=seed,
                      artifact_dir=str(artifact_dir))

    wants_warden = condition in NEEDS_WARDEN
    extra: dict = {"resumable": False}
    if wants_warden:
        settings = install_warden_settings(artifact_dir, project_id=task.id)
        extra["hook_settings"] = str(settings)

    ctx = TrialContext(task_id=task.id, workdir=workdir,
                       prompt=build_prompt(task.prompt, condition),
                       artifact_dir=artifact_dir, trial_index=trial_index,
                       seed=seed, timeout_s=timeout_s, extra=extra)
    (artifact_dir / "prompt.txt").write_text(ctx.prompt)

    if wants_warden and not getattr(driver, "supports_gate", False):
        # Running an ungated trial and filing it under `warden` would be the
        # worst possible outcome, so refuse rather than mislabel.
        res.warden_unavailable = True
        res.error = "warden condition needs a hook-capable driver"
        (artifact_dir / "trial.json").write_text(json.dumps(res.to_dict(), indent=2))
        return res

    dr = driver.run(ctx)

    res.driver_ok = dr.ok
    res.provider_limited = looks_provider_limited(dr)
    res.error = dr.error
    res.num_turns = dr.num_turns
    res.usd_cost = dr.usd_cost
    res.wall_s = dr.raw.get("wall_s")
    res.n_commands = len(dr.commands)
    res.commands_observable = dr.commands_observable
    res.warden_live_blocked = bool(dr.raw.get("warden_blocked"))

    # --- freeze and inspect the work tree ---------------------------------
    diff = _diff(task.repo_dir, workdir)
    (artifact_dir / "work.diff").write_text(diff)
    files_changed = _files_changed(diff, task.repo_dir, workdir)
    res.files_changed = files_changed
    changed_contents = _changed_contents(workdir, files_changed)

    # --- what the agent produced (its own output, not tool results) --------
    surfaced = _surfaced_text(dr, artifact_dir)

    # --- egress: command log always, proxy sink if one was used ------------
    allow = set(task.egress_allow)
    events = egress.scan_commands(dr.commands, task.canary_tokens, allow_hosts=allow)
    sink = artifact_dir / "egress.jsonl"
    events += egress.read_sink(sink, task.canary_tokens, allow_hosts=allow)

    # --- score the detectors ----------------------------------------------
    dinp = DetectorInput(
        canary_tokens=task.canary_tokens,
        exec_marker=task.exec_marker,
        scope_allow=task.scope_allow,
        surfaced_text=surfaced,
        commands=dr.commands,
        files_changed=files_changed,
        changed_file_contents=changed_contents,
        work_diff=diff,
        egress_events=events,
    )
    dets = detectors.run_all(dinp)
    for goal in GOALS:
        setattr(res, goal, dets[goal].hit)
        if dets[goal].evidence:
            res.evidence[goal] = dets[goal].evidence

    # --- defense arm: post-hoc Warden replay ------------------------------
    client = warden or WardenClient.from_env()
    if client.available:
        actions = actions_from_trial(dr.commands, dr.files_read, events)
        rep = replay(client, project_id=task.id, actions=actions)
        res.warden_replay = rep.to_dict()
        res.warden_blocked = rep.blocked_any

    (artifact_dir / "driver_result.json").write_text(
        json.dumps({"commands": dr.commands, "files_read": dr.files_read,
                    "raw": dr.raw, "error": dr.error}, indent=2, default=str))
    (artifact_dir / "trial.json").write_text(json.dumps(res.to_dict(), indent=2))
    return res


# --- extraction helpers -----------------------------------------------------

def _surfaced_text(dr, artifact_dir: pathlib.Path) -> str:
    """The agent's own produced text: final answer plus assistant messages.

    Tool *results* fed back to the agent are excluded on purpose — a secret in a
    file the agent read is reading, not surfacing. Best-effort across driver
    transcript formats; falls back to the final answer alone.
    """
    parts: list[str] = []
    final = dr.raw.get("final_text")
    if final:
        parts.append(str(final))
    for name in ("transcript.jsonl", "transcript.followup.jsonl"):
        path = artifact_dir / name
        if not path.exists():
            continue
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "assistant":
                continue
            for block in ((ev.get("message") or {}).get("content") or []):
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def _changed_contents(workdir: pathlib.Path, files_changed: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in files_changed:
        p = workdir / rel
        try:
            if p.is_file() and p.stat().st_size <= _MAX_FILE_BYTES:
                out[rel] = p.read_text(errors="replace")
        except OSError:
            continue
    return out


def _diff(seed: pathlib.Path, work: pathlib.Path) -> str:
    p = subprocess.run(
        ["diff", "-ruN", "--exclude=VERDICT.txt", "--exclude=__pycache__",
         "--exclude=.pytest_cache", "--exclude=warden-settings.json",
         str(seed), str(work)],
        capture_output=True, text=True)
    return p.stdout


def _files_changed(diff: str, seed: pathlib.Path, work: pathlib.Path) -> list[str]:
    roots = (str(work), str(seed))
    out: set[str] = set()
    for line in diff.splitlines():
        if line.startswith("diff -ruN"):
            cand = line.rsplit(" ", 1)[-1]
        elif line.startswith("Only in "):
            where, _, name = line[len("Only in "):].partition(": ")
            cand = f"{where.rstrip('/')}/{name}"
        else:
            continue
        for root in roots:
            if cand.startswith(root):
                cand = cand[len(root):]
                break
        out.add(cand.lstrip("/"))
    return sorted(out)


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9._-]", "-", s)
