"""Batch runner.

Trials are independent by construction, so they parallelise, but the vendor
CLIs bill against interactive subscriptions and rate-limit long before a machine
runs out of cores. Concurrency defaults low for that reason and is a flag, not a
constant.

Runs are resumable. A completed trial writes ``trial.json`` into its artifact
directory and a re-run skips anything that already has one, except trials the
provider refused, which are never cached, so a re-run after the quota resets
picks them up. A rate limit halfway through a 200-trial batch should cost the
remaining trials, not the finished ones. Carried over from ProvingGround with
the fields updated for Breach's outcomes.
"""

from __future__ import annotations

import concurrent.futures as cf
import dataclasses
import hashlib
import json
import pathlib
import platform
import subprocess
import sys
import time
from typing import Callable, Iterable

from . import sandbox
from .drivers.base import Driver
from .task import Task
from .trial import TrialResult, run_trial
from .warden import WardenClient


@dataclasses.dataclass
class Job:
    task: Task
    driver_factory: Callable[[], Driver]
    driver_name: str
    condition: str
    trial_index: int
    seed: int


def run_batch(jobs: Iterable[Job], out_dir: pathlib.Path, workroot: pathlib.Path,
              image: str = sandbox.IMAGE, concurrency: int = 3,
              timeout_s: int = 900, resume: bool = True,
              warden: WardenClient | None = None, progress=print) -> list[TrialResult]:
    jobs = list(jobs)
    warden = warden or WardenClient.from_env()
    artifact_root = out_dir / "trials"
    artifact_root.mkdir(parents=True, exist_ok=True)
    workroot.mkdir(parents=True, exist_ok=True)
    results: list[TrialResult] = []
    done = 0
    t0 = time.monotonic()

    def one(job: Job) -> TrialResult | None:
        slug = _slug(job)
        cached = artifact_root / slug / "trial.json"
        if resume and cached.exists():
            prior = TrialResult(**_prune(json.loads(cached.read_text())))
            if not prior.provider_limited:
                return prior
        driver = job.driver_factory()
        return run_trial(job.task, driver, job.condition, job.trial_index,
                         job.seed, workroot, artifact_root, image=image,
                         timeout_s=timeout_s, warden=warden)

    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(one, j): j for j in jobs}
        for fut in cf.as_completed(futures):
            job = futures[fut]
            done += 1
            try:
                r = fut.result()
            except Exception as e:  # a failed trial must not kill the batch
                progress(f"[{done}/{len(jobs)}] ERROR {_slug(job)}: {e}")
                continue
            results.append(r)
            hit = "  <-- ACTED ON PLANT" if (r.trap and r.acted_on_plant) else ""
            progress(f"[{done}/{len(jobs)}] {r.task_id} {r.agent} {r.condition} "
                     f"t{r.trial_index} {'trap' if r.trap else 'ctrl'} "
                     f"{r.goal}={'HIT' if r.acted_on_plant else '-'}{hit}")

    progress(f"batch finished in {time.monotonic() - t0:.0f}s")
    return results


def write_results(results: list[TrialResult], out_dir: pathlib.Path,
                  meta: dict) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.jsonl"
    with path.open("w") as f:
        for r in results:
            f.write(json.dumps(r.to_dict()) + "\n")
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    return path


def read_results(path: pathlib.Path) -> list[TrialResult]:
    return [TrialResult(**_prune(json.loads(line)))
            for line in path.read_text().splitlines() if line.strip()]


def run_metadata(drivers: dict[str, Driver], tasks: list[Task],
                 image: str = sandbox.IMAGE, extra: dict | None = None) -> dict:
    """Everything needed to say what was actually run, and when.

    A finding is about a version on a date. Recording CLI versions, the image
    digest, and a checksum per task is what makes that sentence defensible six
    months later.
    """
    return {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": {"platform": platform.platform(), "machine": platform.machine(),
                 "python": sys.version.split()[0]},
        "docker_image": image,
        "docker_image_id": _docker_image_id(image),
        "git_commit": _git_commit(),
        "warden_configured": WardenClient.from_env().available,
        "drivers": {k: d.describe() for k, d in drivers.items()},
        "tasks": {t.id: {"surface": t.surface, "plant_intent": t.plant_intent,
                         "goal": t.goal, "trap": t.trap, "mechanism": t.mechanism,
                         "twin": t.twin, "checksum": task_checksum(t)}
                  for t in tasks},
        **(extra or {}),
    }


def task_checksum(task: Task) -> str:
    """sha256 over every file in the task, path-ordered.

    If a task is edited, its results stop being comparable to results from
    before the edit, and this is how a reader can tell.
    """
    h = hashlib.sha256()
    for p in sorted(task.root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(task.root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _slug(job: Job) -> str:
    import re
    name = re.sub(r"[^a-zA-Z0-9._-]", "-", job.driver_name)
    return f"{job.task.id}__{name}__{job.condition}__t{job.trial_index}"


_FIELDS = {f.name for f in dataclasses.fields(TrialResult)}


def _prune(d: dict) -> dict:
    return {k: v for k, v in d.items() if k in _FIELDS}


def _docker_image_id(image: str) -> str | None:
    p = subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", image],
                       capture_output=True, text=True)
    return p.stdout.strip() or None


def _git_commit() -> str | None:
    p = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                       cwd=pathlib.Path(__file__).resolve().parent.parent)
    return p.stdout.strip() or None
