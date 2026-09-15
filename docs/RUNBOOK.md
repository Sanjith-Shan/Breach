# Runbook

What to run, in order, and what each step costs. Written so an operator can
repeat it rather than infer it.

## Prerequisites

| need | for |
|---|---|
| Python 3.10+ | the harness itself |
| Docker, running | `breach build`, any real (non-sanity) agent run, `verify()` |
| the Claude Code CLI, authenticated | `claude-sonnet`, `claude-haiku`, `claude-opus` |
| the Codex CLI, authenticated | `codex-gpt55`, `codex-terra` |
| `OPENAI_API_KEY` | `ref-gpt52`, `ref-gpt5mini` |
| `ANTHROPIC_API_KEY` | `ref-sonnet` |
| `WARDEN_CHECK_URL` or `WARDEN_BRIDGE` (optional) | the `warden` condition and the post-hoc Warden replay — see `docs/WARDEN.md` |

Keys are read from the environment, or from a `.env` at the project root
(gitignored, never committed — `breach/drivers/reference.py: load_dotenv`).
Nothing above the Docker line is needed to validate the harness itself; see
[Nothing here needs a model](#nothing-here-needs-a-model-or-docker) below.

## Nothing here needs a model (or Docker)

```bash
pip install -r requirements.txt -r requirements-dev.txt

python3 -m pytest -q                     # harness unit tests, seconds, offline
python3 -m breach sanity                 # every task under tasks/, offline, no Docker (currently 224/224)
```

If either of these fails, no result produced below is trustworthy — this is
why they run first and cost nothing.

The offline selftest goes one step further and exercises the full trial
pipeline — staging, diffing, detector scoring, result-writing — with
zero-cost stand-in drivers instead of a real agent:

```bash
make selftest
# equivalent to:
python3 -m breach --out /tmp/breach-selftest --workroot /tmp/breach-selftest-work \
  run --agents attacker noop --conditions baseline --trials 1
```

`attacker` and `noop` are excluded from `breach report`'s headline tables by
design (`is_sanity = True`), so this produces empty rate tables on purpose —
what it proves is that the pipeline runs end to end, not a susceptibility
number.

## Build the sandbox image

Needed for `verify()` (network-isolated verification), for the `EgressNetwork`
/ `AgentContainer` path, and generally before any real agent run:

```bash
python3 -m breach build            # ~1 min, once
```

## MVP-first run

Ship the smallest slice first, per the project's own build plan: 3 surfaces
(`repo_docs`, `inline_comment`, `tool_output`) × 1 intent (`read_canary`).
If the MVP subset is not already generated:

```bash
python3 scripts/gen_suite.py --mvp
```

then run it:

```bash
python3 -m breach --out results/mvp run \
  --agents claude-sonnet claude-haiku --conditions baseline \
  --trials 3 --concurrency 3

python3 -m breach report results/mvp/results.jsonl --write results/mvp/FINDINGS_TABLES.md
```

Note the flag order: `--out` is a **global** flag and goes **before** `run`;
`--agents`, `--conditions`, `--trials`, `--concurrency` are **subcommand**
flags and go **after** it (`breach/cli.py`).

## Full run

Regenerate the full suite first if it is not already checked in under
`tasks/`:

```bash
python3 scripts/gen_suite.py       # 30 tasks, 15 matched pairs
```

Then run each arm. Conditions and agents are independent flags, so a full
matrix is composed from separate invocations (or one invocation naming
several agents/conditions at once — the runner batches and resumes either
way):

```bash
# baseline, the headline arm
python3 -m breach --out results/main run \
  --agents claude-sonnet claude-haiku codex-gpt55 --conditions baseline \
  --trials 3 --concurrency 3

# the cheap defense
python3 -m breach --out results/main run \
  --agents claude-sonnet claude-haiku codex-gpt55 --conditions sentence \
  --trials 3 --concurrency 3

# the live Warden hook (only for drivers with supports_gate = True;
# see docs/WARDEN.md — needs WARDEN_CHECK_URL or WARDEN_BRIDGE configured)
python3 -m breach --out results/main run \
  --agents claude-sonnet claude-haiku --conditions warden \
  --trials 3 --concurrency 3

# gather every completed trial on disk into one results file
python3 -m breach --out results/main consolidate

python3 -m breach report results/main/results.jsonl --write results/main/FINDINGS_TABLES.md
```

`consolidate` runs no agents. It reads the `trial.json` every completed trial
leaves behind and writes one `results.jsonl` covering every arm that actually
ran, so an arm whose provider became unreachable partway through does not
silently vanish from the record.

## Resuming after a rate limit

A completed trial writes `trial.json` into its artifact directory
(`results/<run>/trials/<slug>/trial.json`); a re-run of the same `--out`
skips anything that already has one. Trials the provider itself refused
(`TrialResult.provider_limited`) are never cached, so re-running after the
quota resets retries exactly those and nothing else:

```bash
# same command as before, same --out — picks up where it left off
python3 -m breach --out results/main run --agents ... --conditions ... --trials 3
```

Pass `--no-resume` to force every trial to re-run regardless of what is
already on disk.

## Reading results

```bash
python3 -m breach report results/main/results.jsonl
```

renders the tables described in `FINDINGS.md`'s scaffold directly from the
results file — susceptibility per (agent, surface) with the matched control,
the H2 surface ranking, the four-goal breakdown, the H3 control floor, the
H4 defense delta, and the Warden post-hoc replay when a Warden endpoint was
configured. `--write <path>` also saves the rendered text.

## Cost expectations

Trials are short, single-ticket tasks against a small repository. The full
suite (30 tasks) at a modest matrix — say, 2 agents × 2 conditions × 3
trials — comes to `30 × 2 × 2 × 3 = 360` trials, in the low hundreds, at an
estimated cost on the order of tens of dollars for the API-billed arms (the
vendor-CLI arms bill against interactive subscriptions instead). The MVP
subset (6 tasks) is a small fraction of that. Every run's actual reported
cost is summed and printed under "Integrity and hygiene" in `breach report`'s
output (`TrialResult.usd_cost`).

## Concurrency

Kept low by default (`--concurrency 3`). Both vendor CLIs bill against
interactive subscriptions and rate-limit well before the host runs out of
cores; the API-billed reference agent tolerates more.

## Things that will bite you

**Codex needs an explicit `-m`.** The configured default model on the
authoring machine is rejected by ChatGPT-account auth with a 400, and
leaving it unset silently zeroes out the arm
(`breach/drivers/codex.py`).

**Codex reads stdin.** Every invocation closes it explicitly; without that a
batch run blocks forever on the first trial.

**Claude Code loads the operator's machine by default.** Without
`--setting-sources ""` and `--strict-mcp-config`, plugins, MCP servers, and
session-start hooks from the host become part of every trial's system
prompt. The harness already passes both; verify by checking that
`fingerprint.mcp_servers` is empty and `fingerprint.hooks_fired` is zero in
any trial's `driver_result.json` (see `METHODOLOGY.md`).

**A usage limit looks like a task failure but is not.** `TrialResult
.provider_limited` trials measure a quota, not an agent; they are excluded
from every rate, reported separately, and never cached.

**A `warden` condition trial with no hook support does not silently run
ungated.** If the selected driver's `supports_gate` is `False`, the job is
skipped before it runs (`breach/cli.py: cmd_run`); if a driver claims
`supports_gate = True` but the hook cannot actually be wired up, the trial
records `warden_unavailable = True` rather than being scored as a baseline
trial under the `warden` label. See `docs/WARDEN.md`.
