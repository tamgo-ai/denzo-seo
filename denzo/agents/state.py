"""
AgentStatus enum — canonical agent states.

Replaces magic strings ("idle", "working", "done", "error", "skipped")
throughout the codebase. Use AgentStatus.IDLE.value when writing to DB,
and compare against AgentStatus enum in Python logic.

Example:
    from denzo.agents.state import AgentStatus
    if agent_status == AgentStatus.WORKING.value:
        ...
"""

from enum import Enum


class AgentStatus(Enum):
    IDLE = "idle"
    WORKING = "working"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"
    STARTING = "starting"  # transient, agent is being launched

    # ── Aliases for backward compat ──────────────────────────────────────────
    @classmethod
    def all_values(cls) -> list[str]:
        """Return all status string values (for DB queries)."""
        return [s.value for s in cls]

    @classmethod
    def terminal_states(cls) -> list[str]:
        """States that mean an agent has finished (success or failure)."""
        return [cls.DONE.value, cls.ERROR.value, cls.SKIPPED.value]

    @classmethod
    def active_states(cls) -> list[str]:
        """States where an agent is considered 'in-flight'."""
        return [cls.WORKING.value, cls.STARTING.value]
