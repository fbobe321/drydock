"""Apply interventions by injecting into the running AgentLoop.

The injection path is the same `_inject_system_note` drydock's own
safety checks use — Admiral is just another source of directives.
Every intervention writes to the audit log.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from drydock.admiral import history
from drydock.admiral.detectors import Finding

if TYPE_CHECKING:
    from drydock.core.agent_loop import AgentLoop


def apply(agent_loop: AgentLoop, finding: Finding) -> None:
    """Inject the finding's directive into the live conversation."""
    agent_loop._inject_system_note(finding.directive)
    history.append("intervention", f"{finding.code} :: {finding.directive[:160]}")
