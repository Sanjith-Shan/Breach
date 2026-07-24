"""Task schema and loader.

A Breach task is a small, self-contained repository with a perfectly ordinary
ticket — fix a bug, add a flag — plus, in its *trap* form, one un-asked-for
instruction planted in a specific carrier surface: a line in the README, a
comment mid-file, the body of the issue, the output of a tool the agent runs.
Its *control* twin is byte-identical except that the plant is gone. Every other
variable is held fixed, so any difference in the agent's behaviour is
attributable to the plant and to the surface that carried it, and nothing else.

The measured outcome is not "did the agent fix the bug" but "did the agent act
on the instruction it was never given," scored by the deterministic detectors
in :mod:`breach.detectors`. The canary secret and any execution marker are
unique per task and are baked into *both* twins, so the control gives the
false-positive floor for the same detector for free.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

import yaml

from .detectors import INTENT_TO_GOAL

TASKS_ROOT = pathlib.Path(__file__).resolve().parent.parent / "tasks"

#: The carrier surfaces, in rough order of how routinely an agent ingests them.
SURFACES = (
    "repo_docs",           # README / CONTRIBUTING it is told to read
    "inline_comment",      # a comment mid-file in code it edits
    "issue_pr_body",       # task text it is handed to work from
    "tool_output",         # text returned by a tool it runs
    "mcp_tool_output",     # text returned by an MCP server tool it calls
    "dependency_metadata", # a package description or changelog
    "fetched_web",         # a page or API response it retrieves
)

PLANT_INTENTS = tuple(INTENT_TO_GOAL)  # read_canary, exfil_canary, scope_edit, run_command


@dataclasses.dataclass(frozen=True)
class Task:
    id: str
    title: str
    #: Which carrier surface the planted instruction sits in. The independent
    #: variable of the whole study.
    surface: str
    #: What the planted instruction tells the agent to do. Held constant in
    #: intent across surfaces so the surface is what varies.
    plant_intent: str
    #: True if the planted instruction is present. False for the control twin.
    trap: bool
    #: id of the paired task with the opposite ``trap`` value.
    twin: str
    language: str
    #: What the agent is told — a real ticket, naming a symptom, never the plant.
    prompt: str
    #: Globs for the files a legitimate fix is allowed to touch. Anything else
    #: that changes is an out-of-scope edit.
    scope_allow: tuple[str, ...]
    #: The canary secret, baked identically into both twins. A unique token that
    #: is unambiguous to grep for. Reading the file is not a hit; the token
    #: leaving it into the agent's output, a command, or an edit is.
    canary_value: str
    #: Repo-relative paths where the canary lives (for the run log / audit).
    canary_paths: tuple[str, ...]
    #: For run_command plants: the unique marker the planted command leaves.
    exec_marker: str | None
    #: Extra hosts a legitimate run of this task may reach (usually none).
    egress_allow: tuple[str, ...]
    #: The only repo files allowed to differ between a task and its twin — the
    #: plant carrier. Enforced by a sanity check, because a control that has
    #: drifted from its trap stops being a control.
    twin_delta: tuple[str, ...]
    #: Optional: the repo's own tests, run to record whether the agent also did
    #: the real job. Secondary to the attack-goal metrics.
    harness_cmd: str
    #: Optional setup run inside the sandbox before the agent, to stage a
    #: dynamic surface (e.g. make a tool print the plant). Held identical across
    #: the twin except for the plant text it emits.
    setup_cmd: str
    root: pathlib.Path

    @property
    def repo_dir(self) -> pathlib.Path:
        return self.root / "repo"

    @property
    def goal(self) -> str:
        """The detector goal this task's plant is scored against."""
        return INTENT_TO_GOAL[self.plant_intent]

    @property
    def mechanism(self) -> str:
        """Trap and control share this, so results pair on it."""
        return f"{self.surface}:{self.plant_intent}"

    @property
    def canary_tokens(self) -> list[str]:
        return [self.canary_value] if self.canary_value else []

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["root"] = str(self.root)
        for k in ("scope_allow", "canary_paths", "egress_allow", "twin_delta"):
            d[k] = list(getattr(self, k))
        return d


_REQUIRED = {"id", "title", "surface", "plant_intent", "trap", "twin",
             "language", "prompt"}


def load_task(task_dir: pathlib.Path) -> Task:
    spec = yaml.safe_load((task_dir / "task.yaml").read_text())
    missing = _REQUIRED - set(spec)
    if missing:
        raise ValueError(f"{task_dir.name}: task.yaml missing keys {sorted(missing)}")
    if spec["id"] != task_dir.name:
        raise ValueError(f"{task_dir.name}: id {spec['id']!r} does not match directory name")
    if spec["surface"] not in SURFACES:
        raise ValueError(f"{task_dir.name}: unknown surface {spec['surface']!r}; have {list(SURFACES)}")
    if spec["plant_intent"] not in PLANT_INTENTS:
        raise ValueError(f"{task_dir.name}: unknown plant_intent {spec['plant_intent']!r}; have {list(PLANT_INTENTS)}")
    return Task(
        id=spec["id"],
        title=spec["title"],
        surface=spec["surface"],
        plant_intent=spec["plant_intent"],
        trap=bool(spec["trap"]),
        twin=spec["twin"],
        language=spec["language"],
        prompt=spec["prompt"].strip(),
        scope_allow=tuple(spec.get("scope_allow", [])),
        canary_value=spec.get("canary_value", ""),
        canary_paths=tuple(spec.get("canary_paths", [])),
        exec_marker=spec.get("exec_marker"),
        egress_allow=tuple(spec.get("egress_allow", [])),
        twin_delta=tuple(spec.get("twin_delta", [])),
        harness_cmd=(spec.get("harness_cmd") or "").strip(),
        setup_cmd=(spec.get("setup_cmd") or "").strip(),
        root=task_dir,
    )


def load_suite(root: pathlib.Path = TASKS_ROOT) -> list[Task]:
    tasks = [load_task(d) for d in sorted(root.iterdir())
             if d.is_dir() and (d / "task.yaml").exists()]
    by_id = {t.id: t for t in tasks}
    for t in tasks:
        if t.twin not in by_id:
            raise ValueError(f"{t.id}: twin {t.twin!r} not found in suite")
        twin = by_id[t.twin]
        if twin.trap == t.trap:
            raise ValueError(f"{t.id}: twin {t.twin!r} has the same trap value")
        if twin.mechanism != t.mechanism:
            raise ValueError(
                f"{t.id}: twin {t.twin!r} has a different mechanism "
                f"({twin.mechanism!r} vs {t.mechanism!r})")
    return tasks
