"""Wash-sale screening under IRC section 1091.

Scope and limits, stated as behaviour rather than prose:

* The window length, the one-year threshold and the set of registrations that
  permanently disallow a loss all come from the reviewed rule pack. No tax rule
  is written as a constant in this module.
* Replacement purchases are matched across every account in the screening
  scope, including retirement accounts. A replacement bought in an IRA or Roth
  IRA permanently disallows the loss and produces no basis adjustment
  (Rev. Rul. 2008-5); a replacement in a taxable account adjusts the
  replacement lot's basis and tacks its holding period.
* Planned purchases are screened alongside history. A buy a manager intends to
  make is invisible to a transaction ledger, and that is exactly how one UMA
  sleeve washes another sleeve's harvested loss. A match against a planned buy
  is a conflict to resolve before trading, not a disallowance to report after.
* Matching is by exact security identifier only. Whether two different
  identifiers are substantially identical is a reviewed policy question, so the
  screener reports EQUIVALENCE_NOT_ASSESSED instead of answering it.
* A screen is never "clear". It returns SCREENED_COMPLETE only when coverage
  spans the whole window for every in-scope account and the forward half of
  the window has already closed; otherwise SCREENED_WITH_GAPS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import CALCULATION_VERSIONS
from ..decisions.rule_registry import TaxRules, default_rules
from ..domain import codes
from ..domain.coverage import Finding
from ..domain.identities import Registration
from ..domain.money import Money, sum_money
from ..domain.snapshots import Snapshot
from ..domain.sleeves import PlannedAcquisition
from ..domain.tax_lots import Transaction
from .lot_gain_loss import LotDisposition

class ScreenStatus(str, Enum):
    SCREENED_COMPLETE = "screened_complete"
    SCREENED_WITH_GAPS = "screened_with_gaps"


@dataclass(frozen=True)
class ReplacementMatch:
    txn_id: str
    account_id: str
    registration: Registration
    trade_date: date
    matched_quantity: Decimal
    disallowed_loss: Money
    treatment: str  # BASIS_ADJUSTMENT | PERMANENT_DISALLOWANCE
    planned: bool = False
    unit_id: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "txn_id": self.txn_id,
            "account_id": self.account_id,
            "registration": self.registration.value,
            "trade_date": self.trade_date.isoformat(),
            "matched_quantity": format(self.matched_quantity, "f"),
            "disallowed_loss": self.disallowed_loss.to_json(),
            "treatment": self.treatment,
            "planned": self.planned,
            "unit_id": self.unit_id,
        }


@dataclass(frozen=True)
class SaleScreen:
    lot_id: str
    account_id: str
    security_id: str
    sale_date: date
    sold_quantity: Decimal
    loss: Money
    matched_quantity: Decimal
    unmatched_quantity: Decimal
    disallowed_loss: Money
    permanently_disallowed: Money
    matches: Tuple[ReplacementMatch, ...]
    window_start: date
    window_end: date
    unit_id: str = ""

    @property
    def planned_conflicts(self) -> Tuple[ReplacementMatch, ...]:
        """Matches against intended purchases: still preventable."""
        return tuple(m for m in self.matches if m.planned)

    @property
    def allowed_loss(self) -> Money:
        return (self.loss - self.disallowed_loss).quantized()

    def to_json(self) -> Dict[str, Any]:
        return {
            "lot_id": self.lot_id,
            "account_id": self.account_id,
            "security_id": self.security_id,
            "sale_date": self.sale_date.isoformat(),
            "sold_quantity": format(self.sold_quantity, "f"),
            "loss": self.loss.to_json(),
            "matched_quantity": format(self.matched_quantity, "f"),
            "unmatched_quantity": format(self.unmatched_quantity, "f"),
            "disallowed_loss": self.disallowed_loss.to_json(),
            "permanently_disallowed": self.permanently_disallowed.to_json(),
            "allowed_loss_subject_to_coverage": self.allowed_loss.to_json(),
            "window": {"start": self.window_start.isoformat(), "end": self.window_end.isoformat()},
            "unit_id": self.unit_id,
            "matches": [m.to_json() for m in self.matches],
            "planned_conflict_count": len(self.planned_conflicts),
        }


@dataclass(frozen=True)
class WashSaleScreenResult:
    status: ScreenStatus
    screens: Tuple[SaleScreen, ...]
    findings: Tuple[Finding, ...]
    scope_account_ids: Tuple[str, ...]
    forward_monitoring_until: Optional[date]
    calculation_version: str = CALCULATION_VERSIONS["wash_sale_screen"]
    rule_bundle_ref: str = ""
    rule_bundle_hash: str = ""

    @property
    def planned_conflicts(self) -> Tuple[SaleScreen, ...]:
        return tuple(s for s in self.screens if s.planned_conflicts)

    @property
    def total_disallowed(self) -> Money:
        return sum_money((s.disallowed_loss for s in self.screens))

    @property
    def is_complete(self) -> bool:
        return self.status is ScreenStatus.SCREENED_COMPLETE

    def to_json(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "calculation_version": self.calculation_version,
            "rule_bundle_ref": self.rule_bundle_ref,
            "rule_bundle_hash": self.rule_bundle_hash,
            "scope_account_ids": list(self.scope_account_ids),
            "forward_monitoring_until": (
                self.forward_monitoring_until.isoformat() if self.forward_monitoring_until else None
            ),
            "total_disallowed_loss": self.total_disallowed.to_json(),
            "planned_conflicts": [
                {"lot_id": s.lot_id, "security_id": s.security_id, "unit_id": s.unit_id,
                 "conflicting_units": sorted({m.unit_id for m in s.planned_conflicts}),
                 "matched_quantity": format(
                     sum((m.matched_quantity for m in s.planned_conflicts), Decimal("0")), "f")}
                for s in self.planned_conflicts
            ],
            "screens": [s.to_json() for s in self.screens],
            "findings": [f.to_json() for f in self.findings],
        }


def _window(sale_date: date, rules: TaxRules) -> Tuple[date, date]:
    days = rules.wash_sale_window_days
    return sale_date - timedelta(days=days), sale_date + timedelta(days=days)


@dataclass(frozen=True)
class _Candidate:
    """A replacement purchase, historical or planned."""

    candidate_id: str
    account_id: str
    trade_date: date
    quantity: Decimal
    planned: bool
    unit_id: str


def _candidate_acquisitions(
    snapshot: Snapshot, security_id: str, scope: Sequence[str],
    start: date, end: date, planned: Sequence[PlannedAcquisition] = (),
) -> List[_Candidate]:
    historical = [
        _Candidate(t.txn_id, t.account_id, t.trade_date, t.quantity, False, "")
        for t in snapshot.transactions
        if t.security_id == security_id and t.account_id in scope
        and t.action.is_acquisition and start <= t.trade_date <= end and t.quantity > 0
    ]
    intended = [
        _Candidate(f"planned:{p.unit_id}:{p.security_id}:{p.trade_date.isoformat()}",
                   p.account_id, p.trade_date, p.quantity, True, p.unit_id)
        for p in planned
        if p.security_id == security_id and p.account_id in scope
        and start <= p.trade_date <= end and p.quantity > 0
    ]
    # Historical purchases are matched first: they have already happened, so
    # they consume replacement shares before an intention does.
    return sorted(historical + intended,
                  key=lambda c: (c.planned, c.trade_date, c.candidate_id))


def screen_wash_sales(
    snapshot: Snapshot,
    dispositions: Sequence[LotDisposition],
    scope_account_ids: Optional[Sequence[str]] = None,
    known_related_account_ids: Sequence[str] = (),
    consumed_txn_quantities: Optional[Dict[str, Decimal]] = None,
    planned_acquisitions: Sequence[PlannedAcquisition] = (),
    unit_of_lot: Optional[Dict[str, str]] = None,
    rules: Optional[TaxRules] = None,
) -> WashSaleScreenResult:
    """Screen proposed or historical loss sales for replacement purchases."""
    rules = rules or default_rules(snapshot.as_of.year)
    scope = tuple(scope_account_ids or snapshot.account_ids())
    findings: List[Finding] = []
    screens: List[SaleScreen] = []
    # Replacement shares are consumed across sales so one purchase cannot
    # disallow the same shares twice.
    consumed: Dict[str, Decimal] = dict(consumed_txn_quantities or {})
    forward_until: Optional[date] = None

    losses = [d for d in dispositions if not d.blocked and d.is_loss]

    for disposition in losses:
        window_start, window_end = _window(disposition.sale_date, rules)
        if window_end > snapshot.as_of:
            forward_until = max(forward_until or window_end, window_end)

        selling_unit = (unit_of_lot or {}).get(disposition.lot_id, "")
        loss_amount = -disposition.gain  # positive magnitude
        loss_per_share = loss_amount.amount / disposition.quantity
        remaining = disposition.quantity
        matches: List[ReplacementMatch] = []
        disallowed = Money.zero(loss_amount.currency)
        permanent = Money.zero(loss_amount.currency)

        for txn in _candidate_acquisitions(
            snapshot, disposition.security_id, scope, window_start, window_end,
            planned_acquisitions,
        ):
            if remaining <= 0:
                break
            already = consumed.get(txn.candidate_id, Decimal("0"))
            available = txn.quantity - already
            if available <= 0:
                continue
            matched = min(available, remaining)
            account = snapshot.account(txn.account_id)
            registration = account.registration if account else Registration.TAXABLE
            # Which registrations permanently disallow a loss is a reviewed rule
            # (Rev. Rul. 2008-5), not a property of the enum.
            treatment = (
                codes.PERMANENT_DISALLOWANCE
                if rules.disallows_permanently(registration.value)
                else codes.BASIS_ADJUSTMENT
            )
            amount = Money(loss_per_share * matched, loss_amount.currency).quantized()
            matches.append(ReplacementMatch(
                txn_id=txn.candidate_id, account_id=txn.account_id, registration=registration,
                trade_date=txn.trade_date, matched_quantity=matched,
                disallowed_loss=amount, treatment=treatment,
                planned=txn.planned, unit_id=txn.unit_id,
            ))
            disallowed = disallowed + amount
            if treatment == codes.PERMANENT_DISALLOWANCE:
                permanent = permanent + amount
                findings.append(Finding.make(
                    codes.PERMANENT_DISALLOWANCE, codes.SEVERITY_LIMITING,
                    "Replacement shares were acquired in a retirement account: the loss is "
                    "permanently disallowed and no basis adjustment is available "
                    "(Rev. Rul. 2008-5).",
                    lot_id=disposition.lot_id, replacement_txn_id=txn.candidate_id,
                    account_id=txn.account_id))
            if txn.planned:
                findings.append(Finding.make(
                    codes.CROSS_UNIT_WASH_CONFLICT, codes.SEVERITY_BLOCKING,
                    "A planned purchase would wash this harvested loss. Resolve before "
                    "trading: defer the buy past the window, source it from another "
                    "security, or drop the loss sale.",
                    lot_id=disposition.lot_id, selling_unit=selling_unit,
                    buying_unit=txn.unit_id or txn.account_id,
                    security_id=disposition.security_id,
                    matched_quantity=format(matched, "f")))
            consumed[txn.candidate_id] = already + matched
            remaining -= matched

        screens.append(SaleScreen(
            lot_id=disposition.lot_id, account_id=disposition.account_id,
            security_id=disposition.security_id, sale_date=disposition.sale_date,
            sold_quantity=disposition.quantity, loss=loss_amount.quantized(),
            matched_quantity=disposition.quantity - remaining, unmatched_quantity=remaining,
            disallowed_loss=disallowed.quantized(), permanently_disallowed=permanent.quantized(),
            matches=tuple(matches), window_start=window_start, window_end=window_end,
            unit_id=selling_unit,
        ))

    # --- coverage assessment --------------------------------------------
    complete = True
    if losses:
        overall_start = min(_window(d.sale_date, rules)[0] for d in losses)
        overall_end = max(_window(d.sale_date, rules)[1] for d in losses)
        effective_end = min(overall_end, snapshot.as_of)

        for account_id in scope:
            interval = snapshot.coverage_for(account_id)
            if interval is None:
                complete = False
                findings.append(Finding.make(
                    codes.COVERAGE_UNKNOWN, codes.SEVERITY_LIMITING,
                    "No transaction history coverage declared; this account was screened "
                    "only against transactions that happen to be present.",
                    account_id=account_id))
            elif not interval.covers(overall_start, effective_end):
                complete = False
                findings.append(Finding.make(
                    codes.COVERAGE_GAP, codes.SEVERITY_LIMITING,
                    f"Transaction history does not span the full "
                    f"{rules.wash_sale_window_days * 2 + 1}-day window for this account.",
                    account_id=account_id,
                    required_from=overall_start.isoformat(),
                    required_to=effective_end.isoformat(),
                    held_from=interval.start.isoformat() if interval.start else "unknown",
                    held_to=interval.end.isoformat() if interval.end else "unknown"))

    for account_id in sorted(set(known_related_account_ids) - set(scope)):
        complete = False
        findings.append(Finding.make(
            codes.UNKNOWN_RELATED_ACCOUNTS, codes.SEVERITY_LIMITING,
            "A related account in the same tax unit was not available for screening; "
            "household-wide clearance cannot be asserted.",
            account_id=account_id))

    if forward_until is not None:
        complete = False
        findings.append(Finding.make(
            codes.FUTURE_WINDOW_OPEN, codes.SEVERITY_LIMITING,
            "The 30-day forward window has not closed. A purchase before this date would "
            "create a wash sale that this screen cannot see; re-screen after it closes.",
            monitor_until=forward_until.isoformat()))

    findings.append(Finding.make(
        codes.EQUIVALENCE_NOT_ASSESSED, codes.SEVERITY_INFO,
        "Matching used exact security identifiers only. Whether any other holding is "
        "substantially identical is a reviewed policy question and was not decided here.",
    ))

    return WashSaleScreenResult(
        status=ScreenStatus.SCREENED_COMPLETE if complete else ScreenStatus.SCREENED_WITH_GAPS,
        screens=tuple(screens),
        findings=tuple(findings),
        scope_account_ids=scope,
        forward_monitoring_until=forward_until,
        rule_bundle_ref=rules.bundle_ref,
        rule_bundle_hash=rules.bundle_hash,
    )
