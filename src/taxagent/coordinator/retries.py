"""Bounded retry policy.

Only transient failures retry. A calculation or schema error is not transient:
retrying it hides a defect and burns the deadline.
"""
from __future__ import annotations

from dataclasses import dataclass


class TransientError(Exception):
    """A failure that may succeed on retry, e.g. a connector timeout."""


class PermanentError(Exception):
    """A failure that will not succeed on retry."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.0   # no sleeping in tests; a worker supplies real backoff

    def delay_for(self, attempt: int) -> float:
        return self.base_delay_seconds * (2 ** (attempt - 1))

    def should_retry(self, attempt: int, error: Exception) -> bool:
        return isinstance(error, TransientError) and attempt < self.max_attempts
