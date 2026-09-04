"""Generate the smart-contract task family (matched trap/control pairs).

    python scripts/gen_contract.py

Writes alongside the default suite under tasks/, with a ``contract__`` id prefix
so the two families coexist. Same matched-pair design; the base repo is a
Solidity vault with a real reentrancy bug instead of a Python merge bug.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _contractlib import build_pair  # noqa: E402
from _taskgen import write_task  # noqa: E402

TASKS_ROOT = pathlib.Path(__file__).resolve().parent.parent / "tasks"

MATRIX = {
    "read_canary": ["repo_docs", "inline_comment", "tool_output"],
    "exfil_canary": ["issue_pr_body"],
}


def _spec(pair: dict, trap: bool, task_id: str, twin: str) -> dict:
    return {
        "id": task_id,
        "title": f"contract {pair['surface']} / {pair['intent']} / {'trap' if trap else 'control'}",
        "surface": pair["surface"],
        "plant_intent": pair["intent"],
        "trap": trap,
        "twin": twin,
        "language": "solidity",
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


def main() -> int:
    n = 0
    for intent, surfaces in MATRIX.items():
        for surface in surfaces:
            pair = build_pair(surface, intent)
            trap_id = f"contract__{surface}__{intent}__trap"
            ctrl_id = f"contract__{surface}__{intent}__control"
            write_task(TASKS_ROOT, trap_id, _spec(pair, True, trap_id, ctrl_id),
                       pair["trap_files"])
            write_task(TASKS_ROOT, ctrl_id, _spec(pair, False, ctrl_id, trap_id),
                       pair["control_files"])
            n += 2
    print(f"wrote {n} contract tasks ({n // 2} matched pairs) to {TASKS_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
