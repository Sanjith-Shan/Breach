"""Helpers for writing a task directory to disk."""

from __future__ import annotations

import pathlib

import yaml


def write_task(tasks_root: pathlib.Path, task_id: str, spec: dict,
               files: dict[str, str]) -> pathlib.Path:
    """Write ``tasks/<id>/task.yaml`` and ``tasks/<id>/repo/<files>``."""
    d = tasks_root / task_id
    repo = d / "repo"
    if repo.exists():
        import shutil
        shutil.rmtree(d)
    repo.mkdir(parents=True)
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        if rel.endswith(".sh"):
            p.chmod(0o755)
    (d / "task.yaml").write_text(
        yaml.safe_dump(spec, sort_keys=False, width=100))
    return d
