# Numbers ledger

**No number appears in `FINDINGS.md` or anywhere outside this repository until
it has a row here with real provenance.**

This is the enforcement mechanism behind that rule, not a formality. A number
copied out of a terminal into a write-up loses the ability to be checked
six months later. A number with a row here can always be traced back to the
exact results file, the exact run, and the exact commit that produced it.

## How to add a row

After a run completes (`python3 -m breach --out results/<name> run ...`),
for every number that will be quoted anywhere outside this repository — a
README headline, a write-up, a talk —
add one row below with real values in every column. A row with any column
still reading `[placeholder]` does not satisfy this rule.

| claim | number | source results file | run date | git commit | verified by |
|---|---|---|---|---|---|
| `[placeholder — e.g. "claude-sonnet trap action rate, repo_docs, read_canary, baseline"]` | `[placeholder — e.g. "X% [lo, hi]"]` | `[placeholder — e.g. results/main/results.jsonl]` | `[placeholder — YYYY-MM-DD]` | `[placeholder — git rev-parse HEAD, short]` | `[placeholder — how it was checked: re-ran breach report and matched, or spot-checked evidence lines in trial.json]` |

*(This table currently has no real rows. It is empty by design — no run has
been executed yet. See `FINDINGS.md` for the scaffold those numbers will
fill in, and `PREREGISTRATION.md` for the analysis plan that determines what
they will mean.)*

## What "verified by" means

Not "I looked at it and it seemed right." A number is verified when either:

1. `python3 -m breach report <the source results file>` was re-run and the
   printed table was diffed against the quoted number, or
2. for a specific trial-level claim (e.g. "the agent surfaced the canary on
   trial N"), the `evidence` field in that trial's `trial.json` was read
   directly and matches the claim.

## Why this file is separate from `FINDINGS.md`

`FINDINGS.md` is a narrative built from a results file and is regenerated
wholesale when a new run supersedes an old one. This ledger is additive and
append-only: a number that was once quoted (in a write-up or a prior
version of `FINDINGS.md`) keeps its row here even after a later run produces
a different, more current number, so anyone checking an old claim can still
find what it was based on and when it stopped being current.
