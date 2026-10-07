"""Deterministic loss-harvest candidate selection.

This is a rule-based selector, not an optimizer. It reports its status as
'feasible', never 'optimal', because a greedy pass over loss lots does not
establish optimality and labelling it so would be a false claim in the
evidence package. A real optimizer plugs in behind the same port.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Sequence, Tuple

from .. import CALCULATION_VERSIONS
from ..domain import codes
from ..domain.constraints import NettingBasis
from ..domain.money import Money
from ..decisions.code_rules import evaluate_lot_restrictions
from ..tax.lot_gain_loss import ProposedSale, calculate_gain_loss
from ..tax.wash_sale_screen import screen_wash_sales
from .interface import Candidate, CandidateTrade, OptimizationProblem


class RuleBasedHarvestSelector:
    name = "rule-based-harvest-selector"
    version = CALCULATION_VERSIONS["harvest_selector"]
    supported_scopes = ("account",)

    def solve(self, problem: OptimizationProblem) -> Candidate:
        snapshot, constraints = problem.snapshot, problem.constraints
        prices = problem.prices
        allowed = {
            d.subject_ref for d in evaluate_lot_restrictions(snapshot, constraints, problem.as_of)
            if d.allowed
        }

        # Score every eligible lot by its per-lot result at the supplied price.
        scored: List[Tuple[Decimal, Any]] = []
        skipped: List[Dict[str, str]] = []
        for lot in snapshot.lots:
            if lot.lot_id not in allowed:
                skipped.append({"lot_id": lot.lot_id, "reason": codes.RESTRICTED_SECURITY})
                continue
            if not lot.basis_known:
                skipped.append({"lot_id": lot.lot_id, "reason": codes.MISSING_BASIS})
                continue
            if lot.acquisition_date is None:
                # Basis alone is not enough: without an acquisition date the
                # holding period is undeterminable, so the disposition would be
                # blocked downstream. Proposing it would be proposing a trade
                # whose tax result cannot be computed.
                skipped.append({"lot_id": lot.lot_id,
                                "reason": codes.MISSING_ACQUISITION_DATE})
                continue
            price = prices.get(lot.security_id)
            if price is None:
                skipped.append({"lot_id": lot.lot_id, "reason": "PRICE_UNAVAILABLE"})
                continue
            market = Money.of(price).times(lot.quantity)
            gain = market - lot.basis
            scored.append((gain.amount, lot))

        scored.sort(key=lambda pair: (pair[0], pair[1].lot_id))  # largest loss first

        budget = constraints.gain_budget
        currency = (budget.amount.currency if budget else "USD")
        remaining_budget = budget.remaining if budget else None
        cash_target = constraints.cash_target
        raised = Money.zero(currency)
        running_gain = Money.zero(currency)
        running_gross_gains = Money.zero(currency)
        trades: List[CandidateTrade] = []
        wash_flagged: List[Dict[str, str]] = []

        for gain_amount, lot in scored:
            if cash_target is not None and raised >= cash_target and gain_amount >= 0:
                # Cash is covered; realizing further gains would serve no objective.
                skipped.append({"lot_id": lot.lot_id, "reason": "CASH_TARGET_MET"})
                continue
            price = Money.of(prices[lot.security_id])
            proceeds = price.times(lot.quantity).quantized()
            gain = Money(gain_amount, currency).quantized()

            # Selling a loss lot that already has a replacement in the window
            # converts the intended benefit into a disallowed loss; flag and skip.
            if gain.is_negative:
                probe = calculate_gain_loss(
                    snapshot,
                    [ProposedSale(lot.account_id, lot.security_id, lot.quantity, price,
                                  lot_id=lot.lot_id, sale_date=problem.as_of)],
                )
                screen = screen_wash_sales(
                    snapshot, probe.dispositions,
                    scope_account_ids=snapshot.account_ids(),
                    planned_acquisitions=problem.planned_acquisitions)
                if screen.screens and screen.screens[0].matched_quantity > 0:
                    conflicted = screen.screens[0].planned_conflicts
                    wash_flagged.append({
                        "lot_id": lot.lot_id,
                        # A planned buy is preventable; a settled one is not.
                        "reason": (codes.CROSS_UNIT_WASH_CONFLICT if conflicted
                                   else codes.WASH_SALE_RISK),
                        "matched_quantity": format(screen.screens[0].matched_quantity, "f"),
                        "conflicting_units": sorted({m.unit_id for m in conflicted}),
                    })
                    continue

            if budget is not None:
                measure = (running_gross_gains + gain) if budget.netting_basis is NettingBasis.GROSS_GAINS \
                    else (running_gain + gain)
                if gain_amount > 0 and measure > remaining_budget:
                    skipped.append({"lot_id": lot.lot_id, "reason": "GAIN_BUDGET_EXHAUSTED"})
                    continue

            trades.append(CandidateTrade(
                account_id=lot.account_id, security_id=lot.security_id, lot_id=lot.lot_id,
                quantity=format(lot.quantity, "f"), price_per_share=price.to_json()))
            raised = raised + proceeds
            running_gain = running_gain + gain
            if not gain.is_negative:
                running_gross_gains = running_gross_gains + gain

        shortfall = None
        if cash_target is not None and raised < cash_target:
            shortfall = (cash_target - raised).quantized()

        diagnostics: Dict[str, Any] = {
            "selection_rule": "largest loss first, restrictions and wash-sale risk excluded",
            "optimality_claimed": False,
            "lots_considered": len(scored),
            "lots_skipped": skipped,
            "wash_sale_flagged": wash_flagged,
            "cash_raised": raised.to_json(),
            "net_realized_gain": running_gain.quantized().to_json(),
            "gross_realized_gains": running_gross_gains.quantized().to_json(),
        }
        if shortfall is not None:
            diagnostics["cash_shortfall"] = shortfall.to_json()
            diagnostics["infeasibility_reason"] = (
                "Cash target cannot be met without breaching the gain budget or a restriction."
            )
            return Candidate(tuple(trades), "infeasible", diagnostics, self.name, self.version)

        return Candidate(tuple(trades), "feasible", diagnostics, self.name, self.version)
