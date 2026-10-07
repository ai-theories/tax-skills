"""Findings and source-coverage intervals.

Coverage describes what transaction history we actually hold. It is the
difference between "no wash sale found" and "no wash sale exists".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Optional, Tuple

from .codes import SEVERITY_BLOCKING, SEVERITY_INFO, SEVERITY_LIMITING


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str
    refs: Tuple[Tuple[str, str], ...] = ()

    @classmethod
    def make(cls, code: str, severity: str, message: str, **refs: Any) -> "Finding":
        return cls(code, severity, message, tuple(sorted((k, str(v)) for k, v in refs.items())))

    @property
    def is_blocking(self) -> bool:
        return self.severity == SEVERITY_BLOCKING

    def to_json(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "refs": {k: v for k, v in self.refs},
        }


@dataclass(frozen=True)
class CoverageInterval:
    """Transaction history held for one account. None start/end means unknown."""

    account_id: str
    start: Optional[date]
    end: Optional[date]
    source_ref: str

    def covers(self, start: date, end: date) -> bool:
        if self.start is None or self.end is None:
            return False
        return self.start <= start and self.end >= end

    def gap_against(self, start: date, end: date):
        """Return (missing_from, missing_to) or None when fully covered."""
        if self.covers(start, end):
            return None
        if self.start is None or self.end is None:
            return (start, end)
        missing_from = start if self.start > start else None
        missing_to = end if self.end < end else None
        return (missing_from or self.end, missing_to or self.start)

    def to_json(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "source_ref": self.source_ref,
        }
