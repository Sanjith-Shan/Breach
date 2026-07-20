"""Breach — a prompt-injection susceptibility suite for coding agents.

Breach measures one thing: when a coding agent works a normal ticket and an
instruction it was never given is planted in something it reads along the way,
how often does it act on that instruction, and does the rate depend on which
*surface* carried the instruction?

It is a measurement harness, not an attack kit. The deliverable is a resistance
score per (agent, surface), reported next to a matched control that gives the
false-positive floor for free, and next to a defense arm that reports how much
of the gap a trust layer closes. Same shape as AgentDojo and InjecAgent; the
new work here is the six-to-eight carrier surfaces, the four deterministic
attack-goal detectors, and the Warden defense wiring.

The harness deliberately reuses the sandboxing, statistics, driver interface,
and batch runner proven out in ProvingGround, so the only genuinely new code is
the part that is genuinely new. See ``docs/`` and the root ``METHODOLOGY.md``.
"""

from __future__ import annotations

__version__ = "0.1.0"
