"""Optimizer port and problem/candidate types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Optional, Protocol, Sequence, Tuple

from ..domain.constraints import ConstraintSet
from ..domain.sleeves import CoordinationUnit, PlannedAcquisition
from ..domain.snapshots import Snapshot


class UnsupportedCapability(Exception):
    """Raised when an engine cannot serve the requested scope or asset class."""

    def __init__(self, code: str, message: str, supported_alternative: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.supported_alternative = supported_alternative


class BudgetUnavailable(Exception):
    """A shared budget is already reserved by another run.

    Distinct from infeasibility: the constraint is satisfiable, but another
    sleeve or workflow holds the capacity right now.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OptimizationProblem:
    snapshot: Snapshot
    constraints: ConstraintSet
    scope: str                    # 'account' | 'household_coordinated' | 'household'
    as_of: date
    prices: Dict[str, str]        # security_id -> decimal string price per share
    objective: str = "harvest_losses"
    engine_config_ref: str = "default"
    # Coordinated runs only: the allocation units and what managers intend to buy.
    units: Tuple[CoordinationUnit, ...] = ()
    planned_acquisitions: Tuple[PlannedAcquisition, ...] = ()


@dataclass(frozen=True)
class CandidateTrade:
    account_id: str
    security_id: str
    lot_id: str
    quantity: str
    price_per_share: str
    side: str = "SELL"

    def to_json(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "security_id": self.security_id,
            "lot_id": self.lot_id,
            "quantity": self.quantity,
            "price_per_share": self.price_per_share,
            "side": self.side,
        }


@dataclass(frozen=True)
class Candidate:
    """A proposed trade list plus the engine's own status.

    solver_status is the engine's claim about itself. Nothing downstream is
    allowed to treat it as verification; the independent validator recomputes.
    """

    trades: Tuple[CandidateTrade, ...]
    solver_status: str            # optimal | feasible | infeasible | failed
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    engine_name: str = ""
    engine_version: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "trades": [t.to_json() for t in self.trades],
            "solver_status": self.solver_status,
            "diagnostics": self.diagnostics,
            "engine": {"name": self.engine_name, "version": self.engine_version},
        }


class OptimizerPort(Protocol):
    name: str
    version: str
    supported_scopes: Tuple[str, ...]

    def solve(self, problem: OptimizationProblem) -> Candidate: ...
