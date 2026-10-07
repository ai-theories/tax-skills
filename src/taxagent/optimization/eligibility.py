"""Which lots an optimizer may sell, and why the others are excluded.

Shared by every engine so that "eligible" means one thing. An optimizer that
decided eligibility for itself could reach a better objective by quietly
relaxing a restriction, and the result would look like a better answer rather
than a broken one.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Sequence, Tuple

from ..decisions.code_rules import evaluate_lot_restrictions
from ..domain import codes
from ..domain.money import Money
from ..tax.lot_gain_loss import ProposedSale, calculate_gain_loss
from ..tax.wash_sale_screen import screen_wash_sales
from .exact_solver import SolverItem
from .interface import OptimizationProblem

#: Exclusions a different allocation could not undo. Used by the bound so a
#: ceiling is never inflated by lots nobody may sell.
HARD_EXCLUSIONS = frozenset({
    codes.RESTRICTED_SECURITY, codes.MISSING_BASIS, codes.MISSING_ACQUISITION_DATE,
    "PRICE_UNAVAILABLE", codes.WASH_SALE_RISK, codes.CROSS_UNIT_WASH_CONFLICT,
})


def eligible_items(problem: OptimizationProblem,
                   screen_wash: bool = True) -> Tuple[List[SolverItem],
                                                      List[Dict[str, Any]],
                                                      List[Dict[str, Any]]]:
    """Sellable lots as solver items, plus the exclusions and wash-sale flags.

    Wash-sale screening happens here rather than after selection because a lot
    whose loss would be disallowed is not an opportunity the optimizer should
    be weighing. Including it and screening later would let the solver report a
    harvest it cannot actually deliver.
    """
    snapshot = problem.snapshot
    prices = problem.prices
    allowed = {
        d.subject_ref
        for d in evaluate_lot_restrictions(snapshot, problem.constraints, problem.as_of)
        if d.allowed
    }

    items: List[SolverItem] = []
    skipped: List[Dict[str, Any]] = []
    flagged: List[Dict[str, Any]] = []

    for lot in sorted(snapshot.lots, key=lambda l: l.lot_id):
        if lot.lot_id not in allowed:
            skipped.append({"lot_id": lot.lot_id, "reason": codes.RESTRICTED_SECURITY})
            continue
        if not lot.basis_known:
            skipped.append({"lot_id": lot.lot_id, "reason": codes.MISSING_BASIS})
            continue
        if lot.acquisition_date is None:
            # Basis alone is not enough: without an acquisition date the holding
            # period is undeterminable and the disposition is blocked downstream.
            skipped.append({"lot_id": lot.lot_id,
                            "reason": codes.MISSING_ACQUISITION_DATE})
            continue
        raw_price = prices.get(lot.security_id)
        if raw_price is None:
            skipped.append({"lot_id": lot.lot_id, "reason": "PRICE_UNAVAILABLE"})
            continue

        price = Money.of(raw_price)
        proceeds = price.times(lot.quantity).quantized()
        gain = (proceeds - lot.basis).quantized()

        if screen_wash and gain.is_negative:
            probe = calculate_gain_loss(
                snapshot,
                [ProposedSale(lot.account_id, lot.security_id, lot.quantity, price,
                              lot_id=lot.lot_id, sale_date=problem.as_of)])
            screen = screen_wash_sales(
                snapshot, probe.dispositions,
                scope_account_ids=snapshot.account_ids(),
                planned_acquisitions=problem.planned_acquisitions)
            if screen.screens and screen.screens[0].matched_quantity > 0:
                conflicted = screen.screens[0].planned_conflicts
                flagged.append({
                    "lot_id": lot.lot_id,
                    # A planned buy is preventable; a settled one is not.
                    "reason": (codes.CROSS_UNIT_WASH_CONFLICT if conflicted
                               else codes.WASH_SALE_RISK),
                    "matched_quantity": format(screen.screens[0].matched_quantity, "f"),
                    "conflicting_units": sorted({m.unit_id for m in conflicted}),
                })
                continue

        items.append(SolverItem(
            lot_id=lot.lot_id, cash=proceeds.amount, gain=gain.amount,
            account_id=lot.account_id,
            unit_id=snapshot.tax_unit_for_account(lot.account_id) or ""))

    return items, skipped, flagged


def trades_for(problem: OptimizationProblem, chosen: Sequence[str]):
    """Turn chosen lot ids into candidate trades, in a stable order."""
    from .interface import CandidateTrade

    selected = set(chosen)
    trades = []
    for lot in sorted(problem.snapshot.lots, key=lambda l: l.lot_id):
        if lot.lot_id not in selected:
            continue
        price = Money.of(problem.prices[lot.security_id])
        trades.append(CandidateTrade(
            account_id=lot.account_id, security_id=lot.security_id, lot_id=lot.lot_id,
            quantity=format(lot.quantity, "f"), price_per_share=price.to_json()))
    return tuple(trades)
