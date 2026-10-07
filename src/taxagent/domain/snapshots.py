"""Immutable portfolio snapshots.

A snapshot supports reproducible analysis. It does not prove a scenario is
still executable: prices, balances, open orders and corporate actions move
after the freeze, which is why dependent scenarios are invalidated rather than
quietly recomputed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional, Tuple

from .coverage import CoverageInterval, Finding
from .hashing import content_hash
from .identities import Account
from .sleeves import Sleeve
from .tax_lots import TaxLot, Transaction


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    tenant_id: str
    subject_id: str
    as_of: date
    created_at: datetime
    accounts: Tuple[Account, ...]
    lots: Tuple[TaxLot, ...]
    transactions: Tuple[Transaction, ...]
    coverage: Tuple[CoverageInterval, ...]
    findings: Tuple[Finding, ...]
    source_refs: Tuple[str, ...]
    content_hash: str
    sleeves: Tuple[Sleeve, ...] = ()
    # (lot_id, sleeve_id) pairs. Sleeve membership is an overlay convention;
    # the lot itself remains account-level for tax purposes.
    sleeve_assignments: Tuple[Tuple[str, str], ...] = ()

    # --- lookups ---------------------------------------------------------
    def account(self, account_id: str) -> Optional[Account]:
        return next((a for a in self.accounts if a.account_id == account_id), None)

    def account_ids(self) -> Tuple[str, ...]:
        return tuple(a.account_id for a in self.accounts)

    def lot(self, lot_id: str) -> Optional[TaxLot]:
        return next((l for l in self.lots if l.lot_id == lot_id), None)

    def lots_for(self, account_id: str, security_id: Optional[str] = None) -> Tuple[TaxLot, ...]:
        return tuple(
            l for l in self.lots
            if l.account_id == account_id and (security_id is None or l.security_id == security_id)
        )

    def coverage_for(self, account_id: str) -> Optional[CoverageInterval]:
        return next((c for c in self.coverage if c.account_id == account_id), None)

    def position_quantity(self, account_id: str, security_id: str) -> Decimal:
        return sum((l.quantity for l in self.lots_for(account_id, security_id)), Decimal("0"))

    def sleeve(self, sleeve_id: str) -> Optional[Sleeve]:
        return next((s for s in self.sleeves if s.sleeve_id == sleeve_id), None)

    def sleeve_for_lot(self, lot_id: str) -> Optional[str]:
        return next((sid for lid, sid in self.sleeve_assignments if lid == lot_id), None)

    def lots_in_sleeve(self, sleeve_id: str) -> Tuple[TaxLot, ...]:
        assigned = {lid for lid, sid in self.sleeve_assignments if sid == sleeve_id}
        return tuple(l for l in self.lots if l.lot_id in assigned)

    def tax_unit_for_account(self, account_id: str) -> Optional[str]:
        account = self.account(account_id)
        return account.tax_unit_id if account else None

    def blocking_findings(self) -> Tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.is_blocking)

    def accounts_missing_basis(self) -> Tuple[str, ...]:
        return tuple(sorted({l.account_id for l in self.lots if not l.basis_known}))

    def to_json(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "as_of": self.as_of.isoformat(),
            "accounts": [
                {
                    "account_id": a.account_id,
                    "tax_unit_id": a.tax_unit_id,
                    "registration": a.registration.value,
                    "custodian": a.custodian,
                    "owner_ref": a.owner_ref,
                }
                for a in self.accounts
            ],
            "lots": [l.to_json() for l in self.lots],
            "transactions": [t.to_json() for t in self.transactions],
            "coverage": [c.to_json() for c in self.coverage],
            "findings": [f.to_json() for f in self.findings],
            "source_refs": list(self.source_refs),
            "sleeves": [s.to_json() for s in self.sleeves],
            "sleeve_assignments": [list(pair) for pair in self.sleeve_assignments],
        }


def compute_snapshot_hash(payload: Dict[str, Any]) -> str:
    """Hash the frozen content only; identity and wall-clock time are excluded
    so that the same source data always yields the same hash."""
    material = {k: v for k, v in payload.items() if k not in {"snapshot_id"}}
    return content_hash(material)
