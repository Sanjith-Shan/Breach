"""Driver registry.

Adding an agent is one line here and one small module. Everything else in the
harness is agent-agnostic on purpose, because the only claim worth making about
injection susceptibility is one that survives a change of vendor. The lineup is
carried over from ProvingGround: two vendor CLIs (the products people actually
run), and one reference tool-calling loop pointed at either provider, which
holds the scaffold flat so a difference can be attributed to the model rather
than to the harness around it.
"""

from __future__ import annotations

from .base import Driver, DriverResult, TrialContext
from .claude_code import ClaudeCodeDriver
from .codex import CodexDriver
from .reference import ReferenceAgentDriver
from .sanity import AttackerDriver, CompliantDriver, NoopDriver

#: Agents evaluated in the headline result. Keys are stable; they end up in
#: every results file.
AGENTS: dict[str, callable] = {
    "claude-sonnet": lambda: ClaudeCodeDriver(model="sonnet", label="claude-code:sonnet"),
    "claude-haiku": lambda: ClaudeCodeDriver(model="haiku", label="claude-code:haiku"),
    "claude-opus": lambda: ClaudeCodeDriver(model="opus", label="claude-code:opus"),
    "codex-gpt55": lambda: CodexDriver(model="gpt-5.5", label="codex:gpt-5.5"),
    "codex-terra": lambda: CodexDriver(model="gpt-5.6-terra", label="codex:gpt-5.6-terra"),
    "ref-gpt52": lambda: ReferenceAgentDriver(model="gpt-5.2", provider="openai",
                                              label="reference:gpt-5.2"),
    "ref-gpt5mini": lambda: ReferenceAgentDriver(model="gpt-5-mini", provider="openai",
                                                 label="reference:gpt-5-mini"),
    "ref-sonnet": lambda: ReferenceAgentDriver(model="claude-sonnet-5", provider="anthropic",
                                               label="reference:sonnet-5"),
}

#: Deterministic, zero-cost stand-ins for validating the pipeline. Constructed
#: per task where they need to know the task's plant. Excluded from headline
#: tables by ``is_sanity``.
SANITY: dict[str, callable] = {
    "noop": lambda task=None: NoopDriver(),
    "compliant": lambda task=None: CompliantDriver(task),
    "attacker": lambda task=None: AttackerDriver(task),
}

__all__ = ["Driver", "DriverResult", "TrialContext", "AGENTS", "SANITY",
           "ClaudeCodeDriver", "CodexDriver", "ReferenceAgentDriver",
           "NoopDriver", "CompliantDriver", "AttackerDriver"]
