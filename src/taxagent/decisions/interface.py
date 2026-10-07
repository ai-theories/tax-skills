"""Business-decision evaluation port.

A rules engine is deterministic; that does not make its content tax law. Rule
bundles are versioned, reviewed artifacts and every decision carries a reason
code so the outcome can be explained and audited.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol, Sequence, Tuple


@dataclass(frozen=True)
class Decision:
    subject_ref: str
    allowed: bool
    reason_code: str
    message: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "subject_ref": self.subject_ref,
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "message": self.message,
        }


class DecisionPort(Protocol):
    def evaluate(self, facts: Dict[str, Any], rule_bundle_ref: str) -> Sequence[Decision]: ...
