"""UMA sleeves and coordination units.

A sleeve is a manager's slice of one custodial account. The distinction that
governs every rule below:

* For **allocation**, a sleeve behaves like a separate portfolio: its own
  manager, model, restrictions and share of a budget.
* For **tax**, a sleeve is not separate at all. Every sleeve in an account
  belongs to the same taxpayer, so a loss sold in one sleeve and repurchased in
  another is a wash sale with no internal netting available. The overlay must
  prevent it, not reconcile it afterwards.

Household accounts are the mirror image: separate for tax when they belong to
different tax units, and separate for allocation only when the advisor says so.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class UnitKind(str, Enum):
    ACCOUNT = "account"
    SLEEVE = "sleeve"


@dataclass(frozen=True)
class Sleeve:
    sleeve_id: str
    account_id: str
    manager_ref: str
    model_ref: str
    target_weight: Optional[str] = None      # decimal string, e.g. "0.35"

    def to_json(self) -> Dict[str, Any]:
        return {
            "sleeve_id": self.sleeve_id, "account_id": self.account_id,
            "manager_ref": self.manager_ref, "model_ref": self.model_ref,
            "target_weight": self.target_weight,
        }


@dataclass(frozen=True)
class CoordinationUnit:
    """One allocation unit in a coordinated run: an account or a sleeve."""

    unit_id: str
    kind: UnitKind
    account_id: str
    tax_unit_id: str
    lot_ids: Tuple[str, ...]
    manager_ref: str = ""
    priority: Optional[int] = None           # advisor-supplied ordering, if any

    def to_json(self) -> Dict[str, Any]:
        return {
            "unit_id": self.unit_id, "kind": self.kind.value,
            "account_id": self.account_id, "tax_unit_id": self.tax_unit_id,
            "manager_ref": self.manager_ref, "priority": self.priority,
            "lot_count": len(self.lot_ids),
        }


@dataclass(frozen=True)
class PlannedAcquisition:
    """A buy a manager intends to make.

    Passed into a run so the screener can see it. A planned buy is invisible to
    transaction history, which is exactly how one sleeve washes another
    sleeve's harvested loss.
    """

    account_id: str
    security_id: str
    quantity: Decimal
    trade_date: Any                           # datetime.date
    unit_id: str = ""
    source: str = "manager_model"

    def to_json(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id, "security_id": self.security_id,
            "quantity": format(self.quantity, "f"),
            "trade_date": self.trade_date.isoformat(),
            "unit_id": self.unit_id, "source": self.source,
        }
