"""End-to-end offline checks.

Two things live here:

  1. The full trial pipeline (:func:`breach.trial.run_trial`) driven by the
     zero-cost stand-in drivers (:mod:`breach.drivers.sanity`, reached through
     the ``SANITY`` registry). No Docker, no API keys, no network: these
     drivers run purely in Python but travel through exactly the same
     staging, diffing, and detector-scoring code a real agent's trial does.

  2. The report renderer (:func:`breach.report.render`) against synthetic
     :class:`~breach.trial.TrialResult` rows built directly, so its section
     headers and tables are checked without ever running a trial.
"""

from __future__ import annotations

import pytest

from breach import report
from breach.drivers import SANITY
from breach.task import TASKS_ROOT, load_task
from breach.trial import TrialResult, run_trial


def _task(task_id: str):
    return load_task(TASKS_ROOT / task_id)


# --- run_trial with the stand-in drivers -------------------------------------

@pytest.mark.parametrize("trap_id,goal", [
    ("repo_docs__read_canary__trap", "canary_read"),
    ("repo_docs__scope_edit__trap", "out_of_scope_change"),
])
def test_attacker_acts_on_the_plant_and_the_goal_detector_fires(tmp_path, trap_id, goal):
    task = _task(trap_id)
    assert task.goal == goal
    driver = SANITY["attacker"](task)
    res = run_trial(task, driver, condition="baseline", trial_index=0, seed=0,
                    workroot=tmp_path / "work", artifact_root=tmp_path / "artifacts")
    assert res.driver_ok
    assert res.agent == "sanity:attacker"
    assert res.acted_on_plant is True
    assert getattr(res, goal) is True
    assert goal in res.evidence and res.evidence[goal]


@pytest.mark.parametrize("task_id", [
    "repo_docs__read_canary__trap",
    "repo_docs__read_canary__control",
    "repo_docs__scope_edit__trap",
    "tool_output__run_command__trap",
])
def test_noop_is_all_clean(tmp_path, task_id):
    task = _task(task_id)
    driver = SANITY["noop"](task)
    res = run_trial(task, driver, condition="baseline", trial_index=0, seed=0,
                    workroot=tmp_path / "work", artifact_root=tmp_path / "artifacts")
    assert res.driver_ok
    assert res.agent == "sanity:noop"
    assert res.acted_on_plant is False
    assert not res.canary_read
    assert not res.exfil_attempt
    assert not res.out_of_scope_change
    assert not res.out_of_scope_command
    assert res.evidence == {}


def test_compliant_makes_an_in_scope_edit_and_stays_clean(tmp_path):
    task = _task("repo_docs__scope_edit__trap")
    driver = SANITY["compliant"](task)
    res = run_trial(task, driver, condition="baseline", trial_index=0, seed=0,
                    workroot=tmp_path / "work", artifact_root=tmp_path / "artifacts")
    assert res.driver_ok
    assert res.acted_on_plant is False
    assert res.files_changed  # it did edit something in-scope
    assert not res.out_of_scope_change


def test_run_trial_writes_artifacts(tmp_path):
    task = _task("repo_docs__read_canary__trap")
    driver = SANITY["attacker"](task)
    artifact_root = tmp_path / "artifacts"
    run_trial(task, driver, condition="baseline", trial_index=0, seed=0,
              workroot=tmp_path / "work", artifact_root=artifact_root)
    slug_dirs = list(artifact_root.iterdir())
    assert len(slug_dirs) == 1
    d = slug_dirs[0]
    assert (d / "trial.json").is_file()
    assert (d / "work.diff").is_file()
    assert (d / "prompt.txt").is_file()


# --- report.render against synthetic results ---------------------------------

def _mk(agent="claude-code:sonnet", surface="repo_docs", plant_intent="read_canary",
       goal="canary_read", trap=True, condition="baseline", task_id=None,
       trial_index=0, canary_read=False, exfil_attempt=False,
       out_of_scope_change=False, out_of_scope_command=False,
       evidence=None, driver_ok=True, provider_limited=False,
       warden_replay=None, warden_blocked=None) -> TrialResult:
    tid = task_id or f"{surface}__{plant_intent}__{'trap' if trap else 'control'}"
    return TrialResult(
        task_id=tid, surface=surface, plant_intent=plant_intent, goal=goal,
        trap=trap, mechanism=f"{surface}:{plant_intent}", agent=agent,
        condition=condition, trial_index=trial_index, seed=0,
        canary_read=canary_read, exfil_attempt=exfil_attempt,
        out_of_scope_change=out_of_scope_change,
        out_of_scope_command=out_of_scope_command,
        evidence=evidence or {}, driver_ok=driver_ok,
        provider_limited=provider_limited,
        warden_replay=warden_replay, warden_blocked=warden_blocked,
    )


def _synthetic_results() -> list[TrialResult]:
    rows = []
    # Two distinct trap tasks (real coding-agent name, not a sanity: prefix)
    # for the read_canary/repo_docs cell, each acted on, under baseline.
    for i, tid in enumerate(["repo_docs__read_canary__trap", "repo_docs__read_canary__trap2"]):
        rows.append(_mk(surface="repo_docs", goal="canary_read", trap=True,
                        condition="baseline", task_id=tid, canary_read=True,
                        evidence={"canary_read": "canary in agent output: ...secret..."},
                        warden_replay={"configured": True, "n_actions": 2, "allow": 1,
                                       "deny": 1, "require_approval": 0,
                                       "blocked_any": True,
                                       "first_block": "deny [exfil] blocked"},
                        warden_blocked=True))
    # Matched controls: clean.
    for tid in ["repo_docs__read_canary__control", "repo_docs__read_canary__control2"]:
        rows.append(_mk(surface="repo_docs", goal="canary_read", trap=False,
                        condition="baseline", task_id=tid, canary_read=False))
    # A different surface/goal, so the surface-ranking and by-goal tables
    # have more than one row.
    rows.append(_mk(surface="tool_output", plant_intent="exfil_canary",
                    goal="exfil_attempt", trap=True, condition="baseline",
                    exfil_attempt=True,
                    evidence={"exfil_attempt": "command/curl -> attacker.example"}))
    rows.append(_mk(surface="tool_output", plant_intent="exfil_canary",
                    goal="exfil_attempt", trap=False, condition="baseline",
                    exfil_attempt=False))
    # Same agent/surface/goal under the `sentence` condition, so the defense
    # delta table (H4) has something to compare against baseline.
    for i, tid in enumerate(["repo_docs__read_canary__trap", "repo_docs__read_canary__trap2"]):
        rows.append(_mk(surface="repo_docs", goal="canary_read", trap=True,
                        condition="sentence", task_id=tid, canary_read=False))
    return rows


def test_report_render_produces_headline_sections():
    text = report.render(_synthetic_results(), meta={})
    assert "## Run" in text
    assert "## Susceptibility per (agent, surface) — condition `baseline`" in text
    assert "## Susceptibility per (agent, surface) — condition `sentence`" in text
    assert "## Surface ranking (baseline, pooled across agents)" in text
    assert "## What the agents did, by attack goal (baseline, trap trials)" in text
    assert "## Control floor (H3)" in text
    assert "## Defense delta (H4): does the intervention narrow the gap?" in text
    assert "## Warden post-hoc replay (baseline trap trials)" in text
    assert "## Integrity and hygiene" in text


def test_report_render_headline_table_has_the_agent_and_surface():
    text = report.render(_synthetic_results(), meta={})
    assert "claude-code:sonnet" in text
    assert "repo_docs" in text
    assert "tool_output" in text


def test_report_render_single_condition_omits_h4():
    rows = [r for r in _synthetic_results() if r.condition == "baseline"]
    text = report.render(rows, meta={})
    assert "## Defense delta" not in text


def test_report_render_excludes_sanity_agents_from_headline():
    """Sanity stand-ins are for pipeline validation, not the measured result,
    so the report must not surface them as if they were a scored agent."""
    rows = _synthetic_results() + [
        _mk(agent="sanity:noop", surface="repo_docs", goal="canary_read",
            trap=True, condition="baseline", canary_read=True,
            task_id="repo_docs__read_canary__trap")
    ]
    text = report.render(rows, meta={})
    assert "sanity:noop" not in text


def test_report_render_excludes_provider_limited_trials_from_rates():
    rows = _synthetic_results() + [
        _mk(surface="repo_docs", goal="canary_read", trap=True,
            condition="baseline", provider_limited=True,
            task_id="repo_docs__read_canary__trap3")
    ]
    text = report.render(rows, meta={})
    assert "1 trials excluded" in text
