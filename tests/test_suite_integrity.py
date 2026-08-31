"""Structural checks on the task suite itself.

These run without Docker and without an API key, which is the point. They are
the part of the quality gate that can live in continuous integration, and they
mirror the sanity check the CLI exposes (``python -m breach sanity``) so a
regression in the suite fails ``pytest`` too, not just a manual invocation.
"""

from __future__ import annotations

import pytest

from breach.sanity import check_task, check_twins
from breach.task import PLANT_INTENTS, SURFACES, TASKS_ROOT, load_suite

TASKS = load_suite(TASKS_ROOT)
IDS = [t.id for t in TASKS]

#: The MVP subset the suite must ship first: three surfaces, one intent
#: (see scripts/gen_suite.py MVP dict).
MVP_SURFACES = {"repo_docs", "inline_comment", "tool_output"}
MVP_INTENT = "read_canary"


def test_the_suite_loads_without_error():
    assert len(TASKS) >= 2
    assert len(TASKS) % 2 == 0  # every task is one half of a matched pair


def test_suite_is_evenly_matched_pairs():
    assert sum(t.trap for t in TASKS) == sum(not t.trap for t in TASKS)
    assert sum(t.trap for t in TASKS) == len(TASKS) // 2


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_every_task_has_a_valid_twin(task):
    by_id = {t.id: t for t in TASKS}
    twin = by_id[task.twin]
    assert twin.twin == task.id, f"{task.id} and {task.twin} are not mutual twins"
    # Opposite trap.
    assert twin.trap != task.trap
    # Same mechanism (surface + plant_intent held fixed across the pair).
    assert twin.mechanism == task.mechanism


def test_every_task_is_paired_exactly_once():
    by_id = {t.id: t for t in TASKS}
    for t in TASKS:
        assert by_id[t.twin].twin == t.id


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_task_repo_exists_on_disk(task):
    assert task.repo_dir.is_dir(), task.repo_dir


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_task_surface_and_intent_are_recognised(task):
    assert task.surface in SURFACES
    assert task.plant_intent in PLANT_INTENTS


def test_mvp_surfaces_are_present_for_read_canary():
    have = {t.surface for t in TASKS if t.plant_intent == MVP_INTENT}
    assert MVP_SURFACES <= have


def test_all_declared_surfaces_appear_somewhere_in_the_suite():
    """Every surface the schema knows about is exercised by at least one task,
    so a surface added to SURFACES without suite coverage is caught here."""
    used = {t.surface for t in TASKS}
    # The full matrix (scripts/gen_suite.py MATRIX) covers every surface for
    # read_canary, so nothing in SURFACES should be entirely unused.
    assert used == set(SURFACES)


# --- the sanity check, run as pytest assertions instead of a CLI print -------

def test_check_task_passes_for_every_task_in_the_suite():
    failed = []
    for t in TASKS:
        for c in check_task(t):
            if not c.ok:
                failed.append(c.line())
    assert not failed, "\n" + "\n".join(failed)


def test_check_twins_passes_for_the_whole_suite():
    failed = []
    for c in check_twins(TASKS):
        if not c.ok:
            failed.append(c.line())
    assert not failed, "\n" + "\n".join(failed)


def test_check_task_and_check_twins_together_cover_the_whole_suite():
    """Every task contributes at least the load-bearing detector-fires /
    detector-clean pair, and every pair contributes both twin checks — so the
    total must scale with the suite, not sit at some number that quietly
    stops changing when tasks are added or removed."""
    n_task_checks = sum(len(check_task(t)) for t in TASKS)
    n_twin_checks = len(check_twins(TASKS))
    assert n_task_checks >= len(TASKS) * 4  # scope, canary/marker, fires, clean
    assert n_twin_checks == len(TASKS)  # two checks per pair, one pair per two tasks
