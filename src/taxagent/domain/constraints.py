"""Constraint sets.

A realized-gain budget and a tax-dollar budget are different limits, and a gain
budget is meaningless until its netting basis and period are known. Unknown
fields stay None and force a clarification rather than a default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .money import Money


class NettingBasis(str, Enum):
    GROSS_GAINS = "gross_gains"
    NET_GAINS = "net_gains"


class BudgetKind(str, Enum):
    REALIZED_GAIN = "realized_gain_budget"
    TAX_DOLLAR = "tax_dollar_budget"


class AmbiguousConstraint(ValueError):
    """Raised when a constraint cannot be compiled without a user decision."""

    def __init__(self, message: str, unresolved_fields: List[str]):
        super().__init__(message)
        self.unresolved_fields = unresolved_fields


@dataclass(frozen=True)
class GainBudget:
    kind: BudgetKind
    amount: Money
    netting_basis: NettingBasis
    period: str                      # e.g. "2026" or "2026-Q4"
    external_realized: Optional[Money] = None   # already consumed outside this run
    loss_carryforward: Optional[Money] = None
    hard: bool = True

    @property
    def remaining(self) -> Money:
        used = self.external_realized or Money.zero(self.amount.currency)
        return self.amount - used

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "amount": self.amount.to_json(),
            "currency": self.amount.currency,
            "netting_basis": self.netting_basis.value,
            "period": self.period,
            "external_realized": self.external_realized.to_json() if self.external_realized else None,
            "loss_carryforward": self.loss_carryforward.to_json() if self.loss_carryforward else None,
            "hard": self.hard,
        }


@dataclass(frozen=True)
class Restriction:
    code: str
    security_ids: Tuple[str, ...] = ()
    account_ids: Tuple[str, ...] = ()
    min_holding_days: Optional[int] = None
    note: str = ""


@dataclass(frozen=True)
class TargetAllocation:
    """Target weights by security, for a rebalance.

    Weights are fractions of the portfolio's market value and must sum to one.
    They are supplied by the advisor: deriving a target from the portfolio
    itself would make any drift the engine measured a function of its own
    assumptions.
    """

    weights: Tuple[Tuple[str, Decimal], ...]
    tolerance: Decimal = Decimal("0")        # no trade below this drift, as a fraction

    def weight_for(self, security_id: str) -> Decimal:
        for security, weight in self.weights:
            if security == security_id:
                return weight
        return Decimal("0")

    @property
    def total(self) -> Decimal:
        return sum((w for _, w in self.weights), Decimal("0"))

    def validate(self) -> None:
        if not self.weights:
            raise AmbiguousConstraint(
                "A rebalance needs target weights. Without them there is no target to "
                "measure drift against, and an engine that invented one would be "
                "rebalancing to its own assumption.")
        if any(w < 0 for _, w in self.weights):
            raise AmbiguousConstraint("A target weight cannot be negative.")
        if abs(self.total - Decimal("1")) > Decimal("0.0001"):
            raise AmbiguousConstraint(
                f"Target weights sum to {self.total}, not 1. A partial target is "
                "ambiguous: it does not say whether the remainder is cash, an "
                "unlisted holding, or an omission.")

    def to_json(self) -> Dict[str, Any]:
        return {"weights": [{"security_id": s, "weight": str(w)} for s, w in self.weights],
                "tolerance": str(self.tolerance)}


@dataclass(frozen=True)
class ConstraintSet:
    constraints_ref: str
    tenant_id: str
    subject_id: str
    cash_target: Optional[Money] = None
    withdrawal_account_id: Optional[str] = None
    gain_budget: Optional[GainBudget] = None
    restrictions: Tuple[Restriction, ...] = ()
    lot_selection_method: str = "SPECIFIC"
    rule_bundle_ref: str = ""
    target_allocation: Optional[TargetAllocation] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "constraints_ref": self.constraints_ref,
            "subject_id": self.subject_id,
            "cash_target": self.cash_target.to_json() if self.cash_target else None,
            "withdrawal_account_id": self.withdrawal_account_id,
            "gain_budget": self.gain_budget.to_json() if self.gain_budget else None,
            "restrictions": [
                {
                    "code": r.code,
                    "security_ids": list(r.security_ids),
                    "account_ids": list(r.account_ids),
                    "min_holding_days": r.min_holding_days,
                    "note": r.note,
                }
                for r in self.restrictions
            ],
            "lot_selection_method": self.lot_selection_method,
            "rule_bundle_ref": self.rule_bundle_ref,
            "target_allocation": (self.target_allocation.to_json()
                                  if self.target_allocation else None),
        }
