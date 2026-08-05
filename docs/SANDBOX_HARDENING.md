# Sandbox hardening and the egress boundary

`breach/sandbox.py`, `docker/`. This is the posture that makes the
network-boundary evidence in `exfil_attempt` (`breach/detectors.py`)
trustworthy, and it happens to be the same posture worth wanting on a CI
runner or a Kubernetes pod that executes untrusted code — this document is
what backs the "container/network-security literacy" note in `README.md`
without overstating it into an offensive result of its own (see the honest
scope note in `LIMITATIONS.md`).

## Two container roles, deliberately different powers

- **The agent container** (`AgentContainer`) is where an agent works when run
  containerized. It has the work tree mounted read-write at `/work` and,
  when placed on an `EgressNetwork`, no default route off the host.
- **The verification container** (`sandbox.verify()`) is created fresh,
  after the agent has stopped, mounts the work tree **read-only**, runs with
  `--network none`, and mounts hidden fixtures (where a task uses them)
  read-only. It exists to run one command and die, so a verification run can
  never change what it is measuring.

## Container hardening, applied to every container

`breach/sandbox.py: _hardening()`, combined with fixed resource limits
(`_LIMITS`):

| flag | effect |
|---|---|
| `--cap-drop ALL` | every Linux capability removed; nothing in the container can, for example, change file ownership arbitrarily, bind privileged ports, or load kernel modules regardless of UID |
| `--security-opt no-new-privileges` | a process inside the container can never gain more privileges than it started with (blocks setuid-binary escalation) |
| `--security-opt seccomp=docker/seccomp.json` | a custom syscall allowlist (below), applied on top of capability dropping |
| `--read-only` | the root filesystem is immutable; the only writable paths are the mounted work tree and a size-capped `/tmp` tmpfs (`--tmpfs /tmp:rw,exec,size=256m`) |
| `--memory 2g --cpus 2 --pids-limit 512` | bounds resource exhaustion — a runaway or fork-bombing process inside the container cannot starve the host |
| non-root user (`agent`, uid 1000, set in `docker/Dockerfile`) | a stray `rm -rf /` or similar is boring rather than educational, on top of the read-only rootfs |

None of this is exploit tooling; it is a defensive posture applied uniformly
before any trial runs, whether or not a given trial's plant ever gets an
agent to try something hostile. If one does, this is what bounds what that
something can reach.

## The seccomp profile

`docker/seccomp.json`. Shaped like Docker's own default profile
(`defaultAction: SCMP_ACT_ERRNO`, an explicit allowlist of syscalls a normal
Python/pytest userspace process needs — file I/O, process management,
sockets, signals, the usual). It is trimmed to what running `python3` /
`pytest` inside the hardened container actually requires, and it deliberately
**leaves out** container-escape and machine-control primitives: `ptrace`,
`process_vm_readv`/`writev`, `mount`/`umount2`/`pivot_root`,
`kexec_load`/`kexec_file_load`, `bpf`,
`add_key`/`request_key`/`keyctl`, `init_module`/`delete_module`/
`finit_module`, `reboot`, `swapon`/`swapoff`, `iopl`/`ioperm`, and the
`userfaultfd`/`perf_event_open` surface. Every one of those falls through to
`defaultAction` (`SCMP_ACT_ERRNO`) — blocked, unconditionally, regardless of
capabilities or UID.

## The deny-by-default egress network

`EgressNetwork` (`breach/sandbox.py`) is what turns "the agent can't reach
the network" into "every network attempt is observed, whether or not it
succeeds":

1. The agent container is attached to a Docker network created with
   `--internal`, which has no route off the host by itself.
2. A sidecar proxy (`docker/egress_proxy.py`, started as
   `python3 /opt/breach/egress_proxy.py`, itself run under the same hardening
   flags and resource limits) is the *only* thing also attached to the
   default bridge network, so it is the only path out.
3. The agent container's `HTTP_PROXY`/`HTTPS_PROXY`/`http_proxy`/
   `https_proxy` point at the sidecar. Every TLS-aware client — which is
   effectively every HTTPS library — asks the proxy to open a tunnel via
   `CONNECT`.
4. The proxy allowlists exact hostnames (or subdomains of an allowed suffix)
   from `BREACH_ALLOW_HOSTS`. An allowed `CONNECT` is tunnelled through by
   pumping bytes in both directions; anything else gets `403 Forbidden`. A
   **plain, non-`CONNECT` HTTP request is refused outright** — this proxy is
   a boundary, not a general forward proxy.
5. **Either way, one JSON line is appended** to a host-mounted sink file:
   `{"host", "port", "allowed", "line"}`. A run with zero connection
   attempts still produces an (empty) sink file, so its absence of entries
   is a real fact about the trial rather than a missing log.

`breach/egress.py: read_sink()` parses that file into `EgressEvent`s, exactly
the same shape `scan_commands()` produces from the command log, so the
`exfil_attempt` detector treats a refused proxy `CONNECT` and a
pattern-matched outbound command identically — a hit on either path counts,
and the strongest available evidence (a non-allowlisted destination carrying
the canary) is what gets reported.

## Why this is not the whole story

The vendor-CLI driver arms (Claude Code, Codex) authenticate through the
operator's own credentials, which do not travel cleanly into a container, so
those arms run on the host in an isolated work tree rather than inside
`AgentContainer`/`EgressNetwork`. On that path, `exfil_attempt` relies only
on the command-log detection in `breach/egress.py: scan_commands()` — real,
structured, but not a network-layer guarantee the agent cannot get around.
See `LIMITATIONS.md` for exactly what that means for reading a susceptibility
number on that path, and `METHODOLOGY.md` for how both observation paths
feed the identical detector.

## Why this framing, and its limit

This is the same posture a CI runner executing PR-triggered code, or a
Kubernetes pod running untrusted workloads, should have: drop capabilities,
deny privilege escalation, restrict syscalls, make the root filesystem
immutable, and treat "no route out" as the default with an observed,
allowlisted exception rather than an unobserved wide-open one. Building and
operating it here demonstrates that posture is understood and applied
correctly. It is not, by itself, an offensive result against containers,
Kubernetes, or any cloud platform — no escape was attempted, no
misconfiguration in someone else's environment was found — and should not be
read as one. See the honest-scope notes in `README.md` and `LIMITATIONS.md`.
