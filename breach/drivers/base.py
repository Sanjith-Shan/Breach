"""The driver interface.

A driver is anything that can be handed a work tree and a ticket and be
expected to come back having changed some code. That includes real coding
agents, and it also includes the three degenerate "agents" used for the sanity
checks -- apply the oracle patch, do nothing, apply the naive patch. Putting
all five behind the same interface is what makes the sanity checks meaningful:
they travel through exactly the same trial machinery, sandbox, and verifiers
that a real agent does.
"""

from __future__ import annotations

import abc
import dataclasses
import pathlib
from typing import Any


@dataclasses.dataclass
class TrialContext:
    task_id: str
    #: Host path to the work tree. In-container drivers see it at /work.
    workdir: pathlib.Path
    #: The full text handed to the agent, condition template already applied.
    prompt: str
    #: Where a driver should write its raw transcript.
    artifact_dir: pathlib.Path
    trial_index: int
    seed: int
    timeout_s: int = 900
    #: Driver specific extras for this trial, for example a settings
    #: file installing the verification gate.
    extra: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class DriverResult:
    ok: bool
    #: Every shell command the agent actually executed, in order, as strings.
    #: Empty for drivers that cannot report this; `commands_observable` then
    #: records that the absence is a limitation of the driver rather than a
    #: fact about the agent.
    commands: list[str] = dataclasses.field(default_factory=list)
    commands_observable: bool = True
    #: Files the agent read, where the driver can tell us.
    files_read: list[str] = dataclasses.field(default_factory=list)
    #: Provider session identifier, so a supervisor can resume the agent
    #: rather than starting a fresh one that has lost its reasoning.
    session_id: str | None = None
    #: True if the agent changed a file during this run.
    edited: bool = False
    num_turns: int | None = None
    usd_cost: float | None = None
    error: str | None = None
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


class Driver(abc.ABC):
    #: Stable identifier used in results files.
    name: str = "driver"
    #: True if this driver is one of the sanity-check stand-ins rather than a
    #: real agent. Sanity drivers are excluded from headline result tables.
    is_sanity: bool = False
    #: Whether this agent can be resumed with a follow-up turn, which the
    #: gated condition requires.
    supports_gate: bool = False

    @abc.abstractmethod
    def run(self, ctx: TrialContext) -> DriverResult:
        ...

    def follow_up(self, ctx: TrialContext, message: str,
                  session_id: str | None) -> DriverResult | None:
        """Send one more turn to a session that has already stopped.

        Returns None if this driver cannot resume, which the harness treats as
        the gate being unavailable rather than as the gate passing.
        """
        return None

    def describe(self) -> dict[str, Any]:
        """Version metadata recorded alongside every result.

        A finding is about a version on a date, not about a company, so this
        is not optional bookkeeping.
        """
        return {"name": self.name}

    def preflight(self) -> tuple[bool, str]:
        """Return (available, reason). Checked before a run starts."""
        return True, "ok"
