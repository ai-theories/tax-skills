"""Run lifecycle.

Transitions are explicit and enforced. Retries live inside a step's attempt
history and do not move the run out of RUNNING, which is why there is no edge
back out of FAILED.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet


class RunState(str, Enum):
    RECEIVED = "RECEIVED"
    NEEDS_INPUT = "NEEDS_INPUT"
    VALIDATED = "VALIDATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    REVIEW_READY = "REVIEW_READY"
    COMPLETED = "COMPLETED"
    INVALIDATED = "INVALIDATED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ALLOWED: Dict[RunState, FrozenSet[RunState]] = {
    RunState.RECEIVED: frozenset({RunState.NEEDS_INPUT, RunState.VALIDATED, RunState.FAILED,
                                  RunState.CANCELLED}),
    RunState.NEEDS_INPUT: frozenset({RunState.RECEIVED, RunState.CANCELLED}),
    RunState.VALIDATED: frozenset({RunState.QUEUED, RunState.BLOCKED, RunState.CANCELLED}),
    RunState.QUEUED: frozenset({RunState.RUNNING, RunState.CANCELLED}),
    RunState.RUNNING: frozenset({RunState.REVIEW_READY, RunState.BLOCKED, RunState.FAILED,
                                 RunState.CANCELLED}),
    # A blocked run resumes through a new validated attempt once inputs are repaired.
    RunState.BLOCKED: frozenset({RunState.VALIDATED, RunState.CANCELLED, RunState.FAILED}),
    RunState.REVIEW_READY: frozenset({RunState.COMPLETED, RunState.INVALIDATED}),
    RunState.COMPLETED: frozenset({RunState.INVALIDATED}),
    RunState.INVALIDATED: frozenset({RunState.VALIDATED}),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}

TERMINAL = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})


class InvalidTransition(RuntimeError):
    pass


def assert_transition(current: RunState, target: RunState) -> None:
    if target not in ALLOWED[current]:
        raise InvalidTransition(f"{current.value} -> {target.value} is not a permitted transition")
