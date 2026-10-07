"""Optimality gap bound for coordinated runs.

Sequential coordination cannot say how much it leaves on the table. This module
computes an upper bound on what *any* allocation of the same shared budget
could raise, so the cost of coordinating rather than optimizing is a measured
number instead of an argument.

## The relaxation

The scarce resource is the gain budget; the objective is cash. Relaxing lots to
be divisible turns that into a fractional knapsack, which greedy solves
exactly: take every loss lot (it raises cash and, under net netting, returns
budget capacity), then fill the remaining capacity with gain lots ranked by
cash raised per dollar of budget consumed.

## What the bound does and does not mean

A gap of zero is a proof: no reallocation of this budget across these lots
raises more cash, so sequential was optimal for this instance.

A gap above zero is **not** a promise. The bound ignores whole-lot trading,
per-unit restrictions interacting with the reallocation, re-screening wash
sales after a different trade set, and round-lot rules. A real joint optimizer
would capture some of the gap, never all of it. Treat it as a ceiling on the
prize, and therefore as the input to deciding whether that prize is worth
building for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..domain.constraints import GainBudget, NettingBasis
from ..domain.money import Money

#: Skip reasons that a different allocation could legitimately have chosen
#: otherwise. Anything else is a hard exclusion and stays excluded from the
#: bound, so the ceiling is never inflated by lots nobody may sell.
REALLOCATABLE_SKIP_REASONS = frozenset({"GAIN_BUDGET_EXHAUSTED", "CASH_TARGET_MET"})


@dataclass(frozen=True)
class BoundLot:
    lot_id: str
    unit_id: str
    cash: Decimal        # proceeds if sold in full
    gain: Decimal        # realized gain if sold in full; negative for a loss

    @property
    def is_loss(self) -> bool:
        return self.gain < 0

    def ratio(self) -> Decimal:
        """Cash raised per dollar of budget consumed."""
        return self.cash / self.gain if self.gain > 0 else Decimal("0")


@dataclass(frozen=True)
class BoundResult:
    bound_cash: Money
    achieved_cash: Money
    currency: str = "USD"
    method: str = "lp_relaxation_fractional_knapsack"
    lots_considered: int = 0
    lots_excluded: int = 0
    binding_resource: str = "gain_budget"
    caveats: Tuple[str, ...] = ()

    @property
    def gap(self) -> Money:
        # Never negative: the bound is a ceiling, and float-free arithmetic
        # means a negative here would signal a defect, not rounding.
        raw = (self.bound_cash - self.achieved_cash).quantized()
        return raw if not raw.is_negative else Money.zero(self.currency)

    @property
    def gap_percent(self) -> Optional[str]:
        if self.bound_cash.amount <= 0:
            return None
        pct = (self.gap.amount / self.bound_cash.amount) * Decimal("100")
        return f"{pct.quantize(Decimal('0.01'))}%"

    @property
    def is_provably_optimal(self) -> bool:
        return self.gap.is_zero

    def to_json(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "binding_resource": self.binding_resource,
            "bound_cash": self.bound_cash.to_json(),
            "achieved_cash": self.achieved_cash.to_json(),
            "gap": self.gap.to_json(),
            "gap_percent": self.gap_percent,
            "provably_optimal": self.is_provably_optimal,
            "lots_considered": self.lots_considered,
            "lots_excluded": self.lots_excluded,
            "caveats": list(self.caveats),
        }


def relaxed_cash_bound(
    lots: Sequence[BoundLot],
    budget: Optional[GainBudget],
    achieved_cash: Money,
    excluded_lot_ids: Iterable[str] = (),
    currency: str = "USD",
    cash_target: Optional[Money] = None,
) -> BoundResult:
    """Upper bound on cash raisable from `lots` within `budget`.

    When a cash target exists the bound is capped at it: no allocation pursues
    cash beyond what was asked for, so an uncapped figure would report a gap
    against a goal nobody had.
    """
    excluded = set(excluded_lot_ids)
    eligible = [lot for lot in lots if lot.lot_id not in excluded]

    caveats = [
        "Lots are treated as divisible; whole-lot trading may not reach this figure.",
        "Wash-sale screening is not re-run against the reallocated trade set.",
        "Per-unit restrictions are applied as exclusions, not re-evaluated.",
    ]

    if budget is None:
        # With no budget there is nothing scarce to allocate: every eligible
        # lot could be sold, so the bound is simply all of the cash.
        total = sum((lot.cash for lot in eligible), Decimal("0"))
        return BoundResult(
            _cap(Money(total, currency), cash_target), achieved_cash.quantized(), currency,
            method="unconstrained", lots_considered=len(eligible),
            lots_excluded=len(excluded),
            binding_resource="none",
            caveats=tuple(caveats))

    external = (budget.external_realized.amount if budget.external_realized
                else Decimal("0"))
    capacity = budget.amount.amount - external
    gross_netting = budget.netting_basis is NettingBasis.GROSS_GAINS

    cash = Decimal("0")
    # Losses and break-even lots raise cash without consuming capacity. Under
    # net netting a loss returns capacity, which is what lets a harvest fund a
    # gain elsewhere in the household.
    for lot in eligible:
        if lot.gain <= 0:
            cash += lot.cash
            if not gross_netting:
                capacity += -lot.gain

    gain_lots = sorted((lot for lot in eligible if lot.gain > 0),
                       key=lambda l: (-l.ratio(), l.lot_id))
    for lot in gain_lots:
        if capacity <= 0:
            break
        take = min(Decimal("1"), capacity / lot.gain)
        cash += lot.cash * take
        capacity -= lot.gain * take

    return BoundResult(
        _cap(Money(cash, currency), cash_target), achieved_cash.quantized(), currency,
        lots_considered=len(eligible), lots_excluded=len(excluded),
        caveats=tuple(caveats))


def _cap(bound: Money, cash_target: Optional[Money]) -> Money:
    if cash_target is not None and bound > cash_target:
        return cash_target.quantized()
    return bound.quantized()


def aggregate_bounds(results: Dict[str, BoundResult], currency: str = "USD") -> Dict[str, Any]:
    """Combine per-tax-unit bounds into one reportable figure.

    Budgets belong to taxpayers, so the ceiling is computed per tax unit and
    summed. Pooling them would invent capacity that no single taxpayer has.
    """
    bound = Money.zero(currency)
    achieved = Money.zero(currency)
    considered = excluded = 0
    caveats: List[str] = []
    for result in results.values():
        bound = bound + result.bound_cash
        achieved = achieved + result.achieved_cash
        considered += result.lots_considered
        excluded += result.lots_excluded
        for caveat in result.caveats:
            if caveat not in caveats:
                caveats.append(caveat)

    combined = BoundResult(bound.quantized(), achieved.quantized(), currency,
                           lots_considered=considered, lots_excluded=excluded,
                           caveats=tuple(caveats))
    payload = combined.to_json()
    payload["by_tax_unit"] = {k: v.to_json() for k, v in sorted(results.items())}
    payload["interpretation"] = (
        "A zero gap proves this allocation was optimal for the relaxed problem. "
        "A positive gap is a ceiling on what a joint optimizer could add, not a "
        "figure it would achieve."
    )
    return payload


def hard_exclusions(per_unit_diagnostics: Sequence[Dict[str, Any]],
                    unit_diagnostics: Sequence[Dict[str, Any]] = ()) -> List[str]:
    """Lot ids no allocation may sell, gathered from selector diagnostics.

    A lot skipped for a missing basis, a restriction or a wash-sale conflict is
    unavailable to any allocation. A lot skipped because the budget was spent,
    or because the cash target was already met, is exactly what a different
    allocation might choose differently, so it stays in the bound.
    """
    excluded: List[str] = []
    for diagnostics in list(per_unit_diagnostics) + list(unit_diagnostics):
        for entry in diagnostics.get("lots_skipped", []):
            if entry.get("reason") not in REALLOCATABLE_SKIP_REASONS:
                excluded.append(entry["lot_id"])
        for entry in diagnostics.get("wash_sale_flagged", []):
            excluded.append(entry["lot_id"])
    return excluded
