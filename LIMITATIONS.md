# Limitations

Written before any run, so that nothing here is a defense assembled after
seeing a number.

## What this measures, and what it does not

It measures whether an agent acts on an instruction it was never given, when
that instruction is planted in something it reads while working a normal
ticket. It does not measure how good an agent is at coding, how well it
resists jailbreaks aimed at disallowed content, or how it behaves against an
attacker with any capability beyond "can place text somewhere the agent will
read it." An agent that scores well here has good instruction hygiene. That
is one property, not a general security posture.

## `fetched_web` and `mcp_tool_output` are simulated locally, not live

The tool surface every driver is pinned to (`breach/drivers/claude_code.py:
TOOLS`, the reference agent's fixed tool list) does not include an actual web
fetch or a live MCP client, so those two surfaces are realized as local
stand-ins: `fetched_web` is a bundled text file a shell script `cat`s, and
`mcp_tool_output` is a static JSON file shaped like an MCP tool's response
(`scripts/_plantlib.py: _carry`). This is deliberate — a suite that depends
on live network access or a live MCP server is neither reproducible nor safe
to hand to another operator — but it means these two rows measure "does the
agent act on a plant sitting in a file shaped like a fetched page / MCP
response," not "does the agent act on a plant that arrived through an actual
HTTP fetch or a real MCP round-trip." An agent that specifically treats live,
network-sourced content with more suspicion than a local file would not show
that distinction here. Read the `fetched_web` and `mcp_tool_output` rows in
`FINDINGS.md` with that substitution in mind.

## Exfiltration detection on the host-driver path is command-log-based, not network-layer

The vendor CLIs (Claude Code, Codex) authenticate through the operator's own
subscription or API credentials, which is awkward to carry into a container,
so those driver arms run on the host in an isolated work tree rather than
inside the sandboxed `AgentContainer` / `EgressNetwork` boundary described in
`docs/SANDBOX_HARDENING.md`. On that path, `exfil_attempt` is detected purely
from the structured command log (`breach/egress.py: scan_commands`) — pattern
matching outbound-network invocations and their parsed hosts — not from an
actual network-layer boundary the agent cannot get around. A sufficiently
unusual exfiltration method that does not match any of the recognized tool
patterns (`_NETWORK_PATTERNS`) and does not parse as a host-shaped string
would not be caught on this path. The egress-proxy sink
(`breach/egress.py: read_sink`) gives a true network-layer signal, but only
for arms run inside `EgressNetwork`. Neither path is trusted alone; both feed
the same detector, and where an arm was run matters when reading a
susceptibility number for that goal.

## Small task count, one base repository

15 mechanisms (`surface` × `plant_intent` pairs), 30 tasks total, built from
one base repository (`cfgkit`, a small Python config-merge library). The
paired trap/control design supports a strong claim about *this shape of
task*: whatever difference appears between a trap and its byte-identical
control is attributable to the plant and its surface, and nothing else. It
does not support a claim about base rates on arbitrary real repositories,
larger codebases, other languages, or tasks with a different shape of
ticket. `cfgkit` is small (a dozen files) and Python-only; whether the same
pattern holds in a large, unfamiliar, or non-Python codebase is untested.

## The plant is worded generically, once

Each plant intent is worded identically across every surface that carries it
(`scripts/_plantlib.py: _plant_text`), by design — that is what isolates the
surface as the independent variable. It also means this suite measures
susceptibility to *one* phrasing per intent, not to the space of ways an
attacker might phrase the same ask. A differently worded plant (more urgent,
more authoritative-sounding, disguised as part of the legitimate ticket)
could produce a different rate. No claim is made about phrasing
sensitivity from this design.

## The suite is not adversarially tested against the detectors

The four detectors are exercised by the sanity check's synthetic "acted on
the plant" inputs (`breach/sanity.py`) and by real trial logs, but nobody has
tried to construct an agent action that evades detection while still
achieving the attack goal — e.g. an exfiltration method with no recognized
network signature, or a canary transformation (partial, re-encoded, split
across turns) that would not appear as an exact substring match. A verifier
that has not been attacked by someone motivated to break it has unknown
holes. `canary_read` and `exfil_attempt` in particular rely on the canary
appearing as an exact string; the sanity check confirms the detector fires
on a straightforward exfiltration and stays quiet on nothing happening, not
that it is robust to evasion.

## The models and CLI versions are a snapshot

`FINDINGS.md` will name the exact agent, CLI, and model versions and the
dates a run was taken. Agent capability and default behavior move quickly,
and a finding here is about a version on a date, not about a vendor or a
model family in general. A small number of agents over a short window is
enough to say a pattern is not one implementation's idiosyncrasy; it is not
enough to say anything about coding agents as a category.

## Warden's live arm depends on a driver flag that may drift

The live `warden` condition installs a `PreToolUse` hook via a CLI settings
flag (`breach/warden.py: install_warden_settings`,
`breach/drivers/claude_code.py: argv`). The exact flag Claude Code expects
for a hook-bearing settings file can vary by CLI version; see
`docs/WARDEN.md` for how to verify it against the installed CLI before
trusting the live-block numbers, and note that the harness fails loudly
(`warden_unavailable = True`, not silently ungated) rather than mislabeling a
trial when a driver does not support gating at all.

## Asking the sentence condition to name nothing is itself untested for leakage

The `sentence` condition (`breach/conditions.py: SENTENCE`) is written to
name no specific surface or secret, on the theory that a defense that only
works when you already know the attack is not a defense. Whether the sentence
generalizes to attack goals or surfaces outside this suite's four/seven is
not tested here — only whether it reduces the rate on the exact suite this
project measures.

## Honest scope, restated

This project closes the "no offensive result on an AI system" gap on the **AI
surface only**. It says nothing about CTF or penetration-testing experience,
macOS internals, or cloud/Kubernetes attack surface, and no claim to the
contrary should be inferred from anything in this repository, including its
hardened sandbox (`docs/SANDBOX_HARDENING.md`), which demonstrates
container/network-security literacy but is not itself an offensive result
against those systems.
