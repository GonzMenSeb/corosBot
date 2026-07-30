"""Typed tool outcomes.

The one thing this module exists to make impossible: reporting "there is nothing"
when the truth is "we could not look". Never return "no products match" when the
UCP endpoint 500'd — KB `docs/lifeseek/SPEC.md` §4.2-4.3.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, model_validator


class ToolOutcome(str, Enum):
    """Eight answers a tool call can give. Collapsing any two of them is the bug.

    OK              upstream answered, and the answer is data
    NOT_ELIGIBLE    upstream answered: the request is outside what this tool serves
    UNAVAILABLE     upstream answered: nothing matches, or it is out of stock
    RATE_LIMITED    we were throttled — nothing was learned
    TIMEOUT         we stopped waiting — nothing was learned, and this is not a "no"
    UPSTREAM_ERROR  upstream broke — nothing was learned
    NO_CAPABILITY   no tool can serve this request at all
    NEEDS_HUMAN     an answer exists, but a person has to authorise it first
    """

    OK = "ok"
    NOT_ELIGIBLE = "not_eligible"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    NO_CAPABILITY = "no_capability"
    NEEDS_HUMAN = "needs_human"

    @property
    def is_ok(self) -> bool:
        return self is ToolOutcome.OK

    @property
    def is_conclusive(self) -> bool:
        """Upstream answered, so a negative answer is real evidence."""
        return self in _CONCLUSIVE

    @property
    def is_transient(self) -> bool:
        """We learned nothing; the same call later may still work."""
        return self in _TRANSIENT

    @property
    def needs_escalation(self) -> bool:
        """Retrying cannot help — the request needs a different tool or a person."""
        return self in _ESCALATION


_CONCLUSIVE = frozenset({ToolOutcome.OK, ToolOutcome.NOT_ELIGIBLE, ToolOutcome.UNAVAILABLE})
_TRANSIENT = frozenset({ToolOutcome.RATE_LIMITED, ToolOutcome.TIMEOUT, ToolOutcome.UPSTREAM_ERROR})
_ESCALATION = frozenset({ToolOutcome.NO_CAPABILITY, ToolOutcome.NEEDS_HUMAN})


class ToolResult(BaseModel):
    """What every tool hands back. Tools return this instead of raising, so a caller
    that forgets to catch cannot turn an upstream failure into a silent empty list."""

    tool: str
    outcome: ToolOutcome
    data: Any = None
    detail: str = ""
    retry_after_s: float | None = None

    @model_validator(mode="after")
    def a_result_cannot_be_blank(self):
        if self.outcome.is_ok and self.data is None:
            raise ValueError(
                f"{self.tool} returned OK with no data. An empty answer is data — pass [] "
                "or {}; None means the call never produced one."
            )
        if not self.outcome.is_ok and not self.detail:
            raise ValueError(
                f"{self.tool} returned {self.outcome.name} with no detail. Whatever the UI "
                "or the next stage tells the user has to come from somewhere."
            )
        return self
