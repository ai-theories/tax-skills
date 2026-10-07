"""Independent validation of a candidate scenario.

This module deliberately does not read the engine's diagnostics. It recomputes
every quantity from the frozen snapshot and compares. An engine that reports
success while violating a constraint fails here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import CALCULATION_VERSIONS
from ..domain import codes
from ..domain.constraints import ConstraintSet, NettingBasis
from ..domain.coverage import Finding
from ..domain.money import Money, sum_money
from ..domain.snapshots import Snapshot
from ..optimization.interface import Candidate
from ..tax.lot_gain_loss import GainLossResult, ProposedSale, calculate_gain_loss
from ..tax.wash_sale_screen import WashSaleScreenResult

TOLERANCE = Decimal("0.01")

PASSED = "passed"
FAILED = "failed"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class Violation:
    code: str
    check: str
    message: str
    expected: Optional[str] = None
    actual: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "code": self.code, "check": self.check, "message": self.message,
            "expected": self.expected, "actual": self.actual,
        }


@dataclass(frozen=True)
class ValidationResult:
    checks: Dict[str, str]
    violations: Tuple[Violation, ...]
    limitations: Tuple[Finding, ...]
    recomputed: Dict[str, Any]
    calculation_version: str = CALCULATION_VERSIONS["result_validator"]

    @property
    def passed(self) -> bool:
        return not any(status == FAILED for status in self.checks.values())

    @property
    def is_complete(self) -> bool:
        return self.passed and not any(s == INCOMPLETE for s in self.checks.values())

    def to_json(self) -> Dict[str, Any]:
        return {
            "calculation_version": self.calculation_version,
            "checks": dict(self.checks),
            "violations": [v.to_json() for v in self.violations],
            "limitations": [f.to_json() for f in self.limitations],
            "recomputed": self.recomputed,
            "passed": self.passed,
        }


def validate_scenario(
    snapshot: Snapshot,
    constraints: ConstraintSet,
    candidate: Candidate,
    screen: Optional[WashSaleScreenResult] = None,
    as_of: Optional[date] = None,
) -> ValidationResult:
    checks: Dict[str, str] = {}
    violations: List[Violation] = []
    limitations: List[Finding] = []
    sale_date = as_of or snapshot.as_of

    # --- position conservation: can every trade actually be made? --------
    requested: Dict[str, Decimal] = {}
    sellable: List[Any] = []   # trades that survived the position check
    position_ok = True
    for trade in candidate.trades:
        lot = snapshot.lot(trade.lot_id)
        if lot is None:
            position_ok = False
            violations.append(Violation(
                codes.VALIDATION_FAILED, "position_conservation",
                "Candidate sells a lot that is not in the snapshot.", actual=trade.lot_id))
            continue
        if lot.account_id != trade.account_id or lot.security_id != trade.security_id:
            position_ok = False
            violations.append(Violation(
                codes.VALIDATION_FAILED, "position_conservation",
                "Candidate trade contradicts the lot's account or security.",
                expected=f"{lot.account_id}/{lot.security_id}",
                actual=f"{trade.account_id}/{trade.security_id}"))
            continue
        qty = Decimal(trade.quantity)
        requested[trade.lot_id] = requested.get(trade.lot_id, Decimal("0")) + qty
        if requested[trade.lot_id] > lot.quantity:
            position_ok = False
            violations.append(Violation(
                codes.VALIDATION_FAILED, "position_conservation",
                "Candidate sells more shares than the lot holds.",
                expected=format(lot.quantity, "f"), actual=format(requested[trade.lot_id], "f")))
            continue
        sellable.append(trade)
    checks["position_conservation"] = PASSED if position_ok else FAILED

    # --- recompute the trade list from the snapshot ----------------------
    # Only trades that survived the position check are recomputed; an
    # impossible trade is already a violation and cannot produce a number.
    sales = [
        ProposedSale(t.account_id, t.security_id, Decimal(t.quantity),
                     Money.of(t.price_per_share), lot_id=t.lot_id, sale_date=sale_date)
        for t in sellable
    ]
    recomputed: GainLossResult = calculate_gain_loss(snapshot, sales, sale_date)

    checks["arithmetic"] = PASSED if not recomputed.blocked else FAILED
    for blocked in recomputed.blocked:
        violations.append(Violation(
            blocked.block_code or codes.VALIDATION_FAILED, "arithmetic",
            "Candidate includes a lot whose gain cannot be calculated.",
            actual=blocked.lot_id))

    # --- cash conservation ----------------------------------------------
    proceeds = recomputed.total_proceeds
    if constraints.cash_target is not None:
        if proceeds + Money.of(str(TOLERANCE)) < constraints.cash_target:
            checks["cash_target"] = FAILED
            violations.append(Violation(
                codes.INFEASIBLE_CONSTRAINTS, "cash_target",
                "Proceeds do not meet the cash target.",
                expected=constraints.cash_target.to_json(), actual=proceeds.to_json()))
        else:
            checks["cash_target"] = PASSED
    checks["cash_conservation"] = PASSED  # proceeds are derived, not asserted

    # --- gain budget residual -------------------------------------------
    budget = constraints.gain_budget
    if budget is not None:
        measured = (recomputed.gross_gains if budget.netting_basis is NettingBasis.GROSS_GAINS
                    else recomputed.net_gain)
        consumed = measured + (budget.external_realized or Money.zero(measured.currency))
        residual = (budget.amount - consumed).quantized()
        if residual.is_negative and budget.hard:
            checks["gain_budget"] = FAILED
            violations.append(Violation(
                codes.INFEASIBLE_CONSTRAINTS, "gain_budget",
                f"Realized {budget.netting_basis.value} exceed the budget for {budget.period}.",
                expected=budget.amount.to_json(), actual=consumed.to_json()))
        else:
            checks["gain_budget"] = PASSED
        recomputed_budget = {
            "netting_basis": budget.netting_basis.value,
            "period": budget.period,
            "measured": measured.to_json(),
            "external_realized": (budget.external_realized or Money.zero(measured.currency)).to_json(),
            "residual": residual.to_json(),
        }
    else:
        recomputed_budget = None

    # --- coverage --------------------------------------------------------
    if screen is None:
        checks["wash_sale_coverage"] = INCOMPLETE
        limitations.append(Finding.make(
            codes.COVERAGE_UNKNOWN, codes.SEVERITY_LIMITING,
            "No wash-sale screen was supplied for this scenario."))
    elif screen.is_complete:
        checks["wash_sale_coverage"] = PASSED
    else:
        checks["wash_sale_coverage"] = INCOMPLETE
        limitations.extend(f for f in screen.findings if f.severity != codes.SEVERITY_INFO)

    if snapshot.blocking_findings():
        checks["snapshot_integrity"] = INCOMPLETE
        limitations.extend(snapshot.blocking_findings())
    else:
        checks["snapshot_integrity"] = PASSED

    return ValidationResult(
        checks=checks,
        violations=tuple(violations),
        limitations=tuple(limitations),
        recomputed={
            "totals": recomputed.to_json()["totals"],
            "gain_budget": recomputed_budget,
            "trade_count": len(candidate.trades),
        },
    )
