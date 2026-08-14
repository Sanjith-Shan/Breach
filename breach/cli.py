"""Command line for the harness.

    python -m breach build      build the sandbox image
    python -m breach sanity     static + synthetic checks over the suite (offline)
    python -m breach run        run agents (or offline stand-ins) over the suite
    python -m breach report     turn a results file into the rate tables
    python -m breach consolidate rebuild results.jsonl from the trials on disk
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from . import sandbox
from .drivers import AGENTS, SANITY
from .runner import Job, run_batch, run_metadata, write_results
from .task import TASKS_ROOT, load_suite

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_WORKROOT = pathlib.Path("/tmp/breach-work")
DEFAULT_RESULTS = ROOT / "results"

#: Stand-in driver names and their stable identifiers, resolvable without a task.
_SANITY_NAMES = {"noop": "sanity:noop", "compliant": "sanity:compliant",
                 "attacker": "sanity:attacker"}


def cmd_build(args) -> int:
    ctx = ROOT / "docker"
    print(f"building {args.image} from {ctx}")
    r = sandbox.build_image(ctx, args.image)
    print(r.combined[-4000:])
    return 0 if r.ok else 1


def cmd_sanity(args) -> int:
    from .sanity import check_task, check_twins
    tasks = _select(load_suite(pathlib.Path(args.task_root).resolve()), args.tasks)
    checks = []
    print(f"sanity check over {len(tasks)} tasks (offline, no sandbox)\n")
    for t in tasks:
        for c in check_task(t):
            print(c.line())
            checks.append(c)
    print()
    for c in check_twins(tasks):
        print(c.line())
        checks.append(c)
    failed = [c for c in checks if not c.ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks pass")
    if failed:
        print("\nFAILURES")
        for c in failed:
            print(c.line())
    return 0 if not failed else 1


def cmd_run(args) -> int:
    from .conditions import CONDITIONS, NEEDS_WARDEN

    tasks = _select(load_suite(pathlib.Path(args.task_root).resolve()), args.tasks)
    known = set(AGENTS) | set(SANITY)
    unknown = [a for a in args.agents if a not in known]
    if unknown:
        print(f"unknown agents {unknown}; have {sorted(known)}", file=sys.stderr)
        return 2
    bad = [c for c in args.conditions if c not in CONDITIONS]
    if bad:
        print(f"unknown conditions {bad}; have {sorted(CONDITIONS)}", file=sys.stderr)
        return 2

    real = [a for a in args.agents if a in AGENTS]
    sane = [a for a in args.agents if a in SANITY]

    # Stand-ins run offline; real agents need the sandbox image.
    if real and not sandbox.image_exists(args.image):
        print(f"image {args.image} not found; run `build` first", file=sys.stderr)
        return 2

    drivers = {a: AGENTS[a]() for a in real}
    for key, d in drivers.items():
        ok, why = d.preflight()
        print(f"preflight {key:<16} {'ok' if ok else 'UNAVAILABLE'}  {why}")
        if not ok:
            return 2

    def supports_warden(agent: str) -> bool:
        return agent in drivers and getattr(drivers[agent], "supports_gate", False)

    jobs = []
    for t in tasks:
        for a in args.agents:
            for c in args.conditions:
                if c in NEEDS_WARDEN and not supports_warden(a):
                    continue
                for i in range(args.trials):
                    if a in AGENTS:
                        factory, name = AGENTS[a], drivers[a].name
                    else:
                        factory = (lambda name=a, task=t: SANITY[name](task))
                        name = _SANITY_NAMES[a]
                    jobs.append(Job(task=t, driver_factory=factory, driver_name=name,
                                    condition=c, trial_index=i, seed=args.seed + i))
    for a in args.agents:
        skipped = [c for c in args.conditions if c in NEEDS_WARDEN and not supports_warden(a)]
        if skipped:
            print(f"note: {a} has no hook support, skipping conditions {skipped}")

    print(f"\n{len(jobs)} trials "
          f"({len(tasks)} tasks x {len(args.agents)} agents x "
          f"{len(args.conditions)} conditions x {args.trials} trials), "
          f"concurrency {args.concurrency}\n")
    if args.dry_run:
        return 0

    out_dir = pathlib.Path(args.out)
    results = run_batch(jobs, out_dir, pathlib.Path(args.workroot),
                        image=args.image, concurrency=args.concurrency,
                        timeout_s=args.timeout, resume=not args.no_resume)
    meta = run_metadata(drivers, tasks, image=args.image,
                        extra={"conditions": args.conditions,
                               "trials_per_cell": args.trials, "seed": args.seed})
    path = write_results(results, out_dir, meta)
    print(f"\nwrote {len(results)} results to {path}\n")
    from .report import render
    print(render(results, meta))
    return 0


def cmd_consolidate(args) -> int:
    import json
    from .runner import _prune
    from .trial import TrialResult
    out_dir = pathlib.Path(args.out)
    trials = sorted((out_dir / "trials").glob("*/trial.json"))
    if not trials:
        print(f"no trials under {out_dir}/trials", file=sys.stderr)
        return 2
    results = [TrialResult(**_prune(json.loads(p.read_text()))) for p in trials]
    path = out_dir / "results.jsonl"
    with path.open("w") as f:
        for r in results:
            f.write(json.dumps(r.to_dict()) + "\n")
    by_agent: dict[str, int] = {}
    for r in results:
        by_agent[r.agent] = by_agent.get(r.agent, 0) + 1
    print(f"wrote {len(results)} trials to {path}")
    for agent, n in sorted(by_agent.items()):
        print(f"  {agent:<24} {n}")
    return 0


def cmd_report(args) -> int:
    import json
    from .report import render
    from .runner import read_results
    results = read_results(pathlib.Path(args.results))
    meta_path = pathlib.Path(args.results).parent / "run_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    text = render(results, meta)
    print(text)
    if args.write:
        pathlib.Path(args.write).write_text(text)
        print(f"\nwrote {args.write}", file=sys.stderr)
    return 0


def _select(tasks, patterns):
    if not patterns:
        return tasks
    import fnmatch
    keep = [t for t in tasks if any(fnmatch.fnmatch(t.id, p) for p in patterns)]
    if not keep:
        raise SystemExit(f"no tasks match {patterns}")
    return keep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="breach")
    ap.add_argument("--image", default=sandbox.IMAGE)
    ap.add_argument("--workroot", default=str(DEFAULT_WORKROOT))
    ap.add_argument("--out", default=str(DEFAULT_RESULTS / "latest"))
    ap.add_argument("--task-root", default=str(TASKS_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build").set_defaults(fn=cmd_build)

    s = sub.add_parser("sanity")
    s.add_argument("--tasks", nargs="*", default=[])
    s.set_defaults(fn=cmd_sanity)

    r = sub.add_parser("run")
    r.add_argument("--agents", nargs="+", required=True)
    r.add_argument("--tasks", nargs="*", default=[])
    r.add_argument("--conditions", nargs="+", default=["baseline"])
    r.add_argument("--trials", type=int, default=3)
    r.add_argument("--concurrency", type=int, default=3)
    r.add_argument("--timeout", type=int, default=900)
    r.add_argument("--seed", type=int, default=1000)
    r.add_argument("--no-resume", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("consolidate")
    c.set_defaults(fn=cmd_consolidate)

    p = sub.add_parser("report")
    p.add_argument("results")
    p.add_argument("--write")
    p.set_defaults(fn=cmd_report)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
