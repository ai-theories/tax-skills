"""Tax lots and transactions.

Unknown basis is None and stays None. It is never defaulted to zero, and no
calculation that depends on it is permitted to produce a number anyway.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional

from .money import Money, parse_quantity


class BasisSource(str, Enum):
    CUSTODIAN_COVERED = "CUSTODIAN_COVERED"
    CUSTODIAN_NONCOVERED = "CUSTODIAN_NONCOVERED"
    CLIENT_SUPPLIED = "CLIENT_SUPPLIED"
    TRANSFERRED_UNVERIFIED = "TRANSFERRED_UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND_REINVEST = "DIVIDEND_REINVEST"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"

    @property
    def is_acquisition(self) -> bool:
        return self in {Action.BUY, Action.DIVIDEND_REINVEST, Action.TRANSFER_IN}


@dataclass(frozen=True)
class TaxLot:
    lot_id: str
    account_id: str
    security_id: str
    quantity: Decimal
    acquisition_date: Optional[date]
    basis: Optional[Money]
    basis_source: BasisSource = BasisSource.UNKNOWN
    covered: Optional[bool] = None

    @property
    def basis_known(self) -> bool:
        return self.basis is not None

    def basis_per_share(self) -> Optional[Decimal]:
        if self.basis is None or self.quantity == 0:
            return None
        return self.basis.amount / self.quantity

    def to_json(self) -> Dict[str, Any]:
        return {
            "lot_id": self.lot_id,
            "account_id": self.account_id,
            "security_id": self.security_id,
            "quantity": format(self.quantity, "f"),
            "acquisition_date": self.acquisition_date.isoformat() if self.acquisition_date else None,
            "basis": self.basis.to_json() if self.basis else None,
            "basis_source": self.basis_source.value,
            "covered": self.covered,
        }


@dataclass(frozen=True)
class Transaction:
    txn_id: str
    account_id: str
    security_id: str
    action: Action
    trade_date: date
    quantity: Decimal
    amount: Optional[Money] = None
    settle_date: Optional[date] = None
    lot_ref: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "txn_id": self.txn_id,
            "account_id": self.account_id,
            "security_id": self.security_id,
            "action": self.action.value,
            "trade_date": self.trade_date.isoformat(),
            "quantity": format(self.quantity, "f"),
            "amount": self.amount.to_json() if self.amount else None,
            "settle_date": self.settle_date.isoformat() if self.settle_date else None,
            "lot_ref": self.lot_ref,
        }


def make_lot(lot_id: str, account_id: str, security_id: str, quantity,
             acquisition_date: Optional[date], basis: Optional[Money],
             basis_source: BasisSource = BasisSource.UNKNOWN,
             covered: Optional[bool] = None) -> TaxLot:
    return TaxLot(lot_id, account_id, security_id, parse_quantity(quantity),
                  acquisition_date, basis, basis_source, covered)
