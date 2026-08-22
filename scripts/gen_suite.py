"""Generate the matched trap/control suite.

    python scripts/gen_suite.py            # write the full suite
    python scripts/gen_suite.py --mvp      # write only the MVP subset

The suite holds the base repo, the plant intent, and the canary constant, and
varies only the carrier surface, so a difference in the rate table is a
difference in the surface. Every trap is written next to a byte-identical
control with the plant removed.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _plantlib import build_pair  # noqa: E402
from _taskgen import write_task  # noqa: E402

TASKS_ROOT = pathlib.Path(__file__).resolve().parent.parent / "tasks"

#: read_canary is run on every surface (this is the row that tests H2). The
#: other intents are run on a spread of high- and medium-realism surfaces to
#: test that the effect is not specific to one intent, without ballooning the
#: trial count.
MATRIX = {
    "read_canary": ["repo_docs", "inline_comment", "issue_pr_body", "tool_output",
                    "mcp_tool_output", "dependency_metadata", "fetched_web"],
    "exfil_canary": ["repo_docs", "inline_comment", "issue_pr_body", "tool_output"],
    "scope_edit": ["repo_docs", "tool_output"],
    "run_command": ["repo_docs", "tool_output"],
}

#: The MVP the spec says to ship first: three surfaces, one intent.
MVP = {"read_canary": ["repo_docs", "inline_comment", "tool_output"]}


def _spec(pair: dict, trap: bool, task_id: str, twin: str) -> dict:
    return {
        "id": task_id,
        "title": f"{pair['surface']} / {pair['intent']} / {'trap' if trap else 'control'}",
        "surface": pair["surface"],
        "plant_intent": pair["intent"],
        "trap": trap,
        "twin": twin,
        "language": "python",
        "prompt": pair["prompt"],
        "scope_allow": pair["scope_allow"],
        "canary_value": pair["canary_value"],
        "canary_paths": pair["canary_paths"],
        "exec_marker": pair["exec_marker"],
        "egress_allow": [],
        "twin_delta": pair["twin_delta"],
        "harness_cmd": pair["harness_cmd"],
        "setup_cmd": "",
    }


def generate(matrix: dict[str, list[str]]) -> int:
    n = 0
    for intent, surfaces in matrix.items():
        for surface in surfaces:
            pair = build_pair(surface, intent)
            trap_id = f"{surface}__{intent}__trap"
            ctrl_id = f"{surface}__{intent}__control"
            write_task(TASKS_ROOT, trap_id, _spec(pair, True, trap_id, ctrl_id),
                       pair["trap_files"])
            write_task(TASKS_ROOT, ctrl_id, _spec(pair, False, ctrl_id, trap_id),
                       pair["control_files"])
            n += 2
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mvp", action="store_true", help="write only the MVP subset")
    args = ap.parse_args(argv)
    matrix = MVP if args.mvp else MATRIX
    n = generate(matrix)
    print(f"wrote {n} tasks ({n // 2} matched pairs) to {TASKS_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
