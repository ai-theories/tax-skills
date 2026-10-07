"""Sequential coordination across household accounts or UMA sleeves.

This is coordination, not joint optimization, and the distinction is load
bearing. Units are processed in a declared order against a shared, decrementing
budget. A different order can produce a different answer, so the order and the
rule that produced it are recorded in the diagnostics and carried into the
evidence package. Nothing here may be described as household-optimal; see
`docs/joint-optimization.md` for what that would require.

What it does provide, which running an account engine twice does not:

1. One gain budget per **tax unit**, decremented across units and reserved so a
   concurrent run cannot spend the same allowance.
2. Wash-sale screening across every account in the tax unit, including planned
   purchases, so one sleeve cannot wash another sleeve's harvested loss.
3. A single combined trade list validated as a whole.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import CALCULATION_VERSIONS
from ..domain import codes
from ..domain.constraints import ConstraintSet, GainBudget, NettingBasis
from ..domain.money import Money
from ..domain.sleeves import CoordinationUnit, UnitKind
from ..domain.snapshots import Snapshot
from .bounds import BoundLot, aggregate_bounds, hard_exclusions, relaxed_cash_bound
from .harvest_selector import RuleBasedHarvestSelector
from .interface import BudgetUnavailable, Candidate, CandidateTrade, OptimizationProblem

RESERVATION_MINUTES = 30


def derive_units(snapshot: Snapshot) -> Tuple[CoordinationUnit, ...]:
    """Sleeves when the snapshot has them, otherwise accounts."""
    if snapshot.sleeves:
        units = []
        for sleeve in snapshot.sleeves:
            lot_ids = tuple(l.lot_id for l in snapshot.lots_in_sleeve(sleeve.sleeve_id))
            units.append(CoordinationUnit(
                unit_id=sleeve.sleeve_id, kind=UnitKind.SLEEVE,
                account_id=sleeve.account_id,
                tax_unit_id=snapshot.tax_unit_for_account(sleeve.account_id) or "",
                lot_ids=lot_ids, manager_ref=sleeve.manager_ref))
        # Lots in an account with sleeves but assigned to none are their own unit:
        # unassigned holdings must not silently disappear from the analysis.
        for account in snapshot.accounts:
            orphans = tuple(
                l.lot_id for l in snapshot.lots_for(account.account_id)
                if snapshot.sleeve_for_lot(l.lot_id) is None)
            if orphans:
                units.append(CoordinationUnit(
                    unit_id=f"{account.account_id}:unsleeved", kind=UnitKind.ACCOUNT,
                    account_id=account.account_id, tax_unit_id=account.tax_unit_id,
                    lot_ids=orphans, manager_ref=""))
        return tuple(units)

    return tuple(
        CoordinationUnit(
            unit_id=account.account_id, kind=UnitKind.ACCOUNT,
            account_id=account.account_id, tax_unit_id=account.tax_unit_id,
            lot_ids=tuple(l.lot_id for l in snapshot.lots_for(account.account_id)))
        for account in snapshot.accounts
    )


class SequentialHouseholdCoordinator:
    name = "household-coordinator"
    version = CALCULATION_VERSIONS.get("household_coordinator", "1.0.0")
    supported_scopes = ("household_coordinated",)

    #: How units are ordered when the advisor supplies no priority. Recorded in
    #: diagnostics because it changes the result.
    DEFAULT_ORDER_RULE = "largest unrealized loss first, then unit_id"

    def __init__(self, selector: Optional[RuleBasedHarvestSelector] = None,
                 reservation_store=None, tenant_id: str = "", run_id: str = ""):
        self.selector = selector or RuleBasedHarvestSelector()
        self.reservation_store = reservation_store
        self.tenant_id = tenant_id
        self.run_id = run_id
        self._reservations: List[str] = []

    # --- ordering --------------------------------------------------------
    def _unrealized_loss(self, snapshot: Snapshot, unit: CoordinationUnit,
                         prices: Dict[str, str]) -> Decimal:
        total = Decimal("0")
        for lot_id in unit.lot_ids:
            lot = snapshot.lot(lot_id)
            if lot is None or not lot.basis_known:
                continue
            price = prices.get(lot.security_id)
            if price is None:
                continue
            gain = Money.of(price).times(lot.quantity) - lot.basis
            if gain.is_negative:
                total += gain.amount
        return total

    def order_units(self, snapshot: Snapshot, units: Sequence[CoordinationUnit],
                    prices: Dict[str, str]) -> Tuple[List[CoordinationUnit], str]:
        if any(u.priority is not None for u in units):
            ordered = sorted(units, key=lambda u: (u.priority is None,
                                                   u.priority or 0, u.unit_id))
            return ordered, "advisor-supplied priority, then unit_id"
        ordered = sorted(units, key=lambda u: (self._unrealized_loss(snapshot, u, prices),
                                               u.unit_id))
        return ordered, self.DEFAULT_ORDER_RULE

    # --- budget ----------------------------------------------------------
    def _reserve(self, tax_unit_id: str, budget: GainBudget, amount: Money) -> None:
        if self.reservation_store is None or amount.amount <= 0:
            return
        expires = (datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_MINUTES)).isoformat()
        ok, detail = self.reservation_store.reserve(
            self.tenant_id, tax_unit_id, budget.period, amount.to_json(),
            self.run_id, budget.amount.to_json(), expires)
        if not ok:
            self.release_all()
            raise BudgetUnavailable(codes.BUDGET_RESERVED_ELSEWHERE, detail)
        self._reservations.append(detail)

    def release_all(self) -> None:
        if self.reservation_store is None:
            return
        for reservation_id in self._reservations:
            self.reservation_store.release(self.tenant_id, reservation_id)
        self._reservations = []

    # --- solve -----------------------------------------------------------
    def solve(self, problem: OptimizationProblem) -> Candidate:
        snapshot = problem.snapshot
        units = list(problem.units) or list(derive_units(snapshot))
        ordered, order_rule = self.order_units(snapshot, units, problem.prices)

        budget = problem.constraints.gain_budget
        currency = budget.amount.currency if budget else "USD"
        cash_target = problem.constraints.cash_target

        consumed_by_tax_unit: Dict[str, Money] = {}
        raised = Money.zero(currency)
        trades: List[CandidateTrade] = []
        per_unit: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []
        unit_diagnostics: List[Dict[str, Any]] = []
        cash_by_tax_unit: Dict[str, Money] = {}

        for unit in ordered:
            unit_snapshot = self._restrict(snapshot, unit)
            remaining_cash = None
            if cash_target is not None:
                remaining_cash = cash_target - raised
                if remaining_cash.amount <= 0:
                    per_unit.append({"unit_id": unit.unit_id, "skipped": "CASH_TARGET_MET",
                                     "trades": 0})
                    continue

            unit_budget = None
            if budget is not None:
                used = consumed_by_tax_unit.get(unit.tax_unit_id, Money.zero(currency))
                # Each unit sees only what the units before it left behind.
                unit_budget = replace(
                    budget,
                    external_realized=(budget.external_realized or Money.zero(currency)) + used)

            unit_constraints = replace(
                problem.constraints, cash_target=remaining_cash, gain_budget=unit_budget)
            unit_problem = replace(
                problem, snapshot=unit_snapshot, constraints=unit_constraints,
                scope="account", units=(), planned_acquisitions=problem.planned_acquisitions)

            candidate = self.selector.solve(unit_problem)
            trades.extend(candidate.trades)
            unit_diagnostics.append(candidate.diagnostics)

            unit_gain = Money.of(candidate.diagnostics["net_realized_gain"], currency)
            measure = (Money.of(candidate.diagnostics["gross_realized_gains"], currency)
                       if budget and budget.netting_basis is NettingBasis.GROSS_GAINS
                       else unit_gain)
            unit_cash = Money.of(candidate.diagnostics["cash_raised"], currency)
            raised = raised + unit_cash
            cash_by_tax_unit[unit.tax_unit_id] = (
                cash_by_tax_unit.get(unit.tax_unit_id, Money.zero(currency)) + unit_cash)
            if budget is not None:
                consumed_by_tax_unit[unit.tax_unit_id] = (
                    consumed_by_tax_unit.get(unit.tax_unit_id, Money.zero(currency)) + measure)

            for flag in candidate.diagnostics.get("wash_sale_flagged", []):
                if flag.get("reason") == codes.CROSS_UNIT_WASH_CONFLICT:
                    conflicts.append({**flag, "selling_unit": unit.unit_id})

            per_unit.append({
                "unit_id": unit.unit_id, "kind": unit.kind.value,
                "account_id": unit.account_id, "tax_unit_id": unit.tax_unit_id,
                "manager_ref": unit.manager_ref,
                "trades": len(candidate.trades),
                "cash_raised": unit_cash.to_json(),
                "budget_consumed": measure.to_json(),
                "solver_status": candidate.solver_status,
            })

        # Reserve what the whole run consumed, per tax unit, before publishing.
        if budget is not None:
            for tax_unit_id, consumed in sorted(consumed_by_tax_unit.items()):
                self._reserve(tax_unit_id, budget, consumed)

        gap = self._optimality_gap(
            snapshot, ordered, problem.prices, budget, currency,
            cash_by_tax_unit, cash_target, unit_diagnostics)

        shortfall = None
        if cash_target is not None and raised < cash_target:
            shortfall = (cash_target - raised).quantized()

        diagnostics: Dict[str, Any] = {
            "coordination": "sequential",
            "optimality_claimed": False,
            "order_rule": order_rule,
            "allocation_order": [u.unit_id for u in ordered],
            "order_dependent": True,
            "per_unit": per_unit,
            "cash_raised": raised.to_json(),
            "budget_consumed_by_tax_unit": {
                k: v.to_json() for k, v in sorted(consumed_by_tax_unit.items())},
            "cross_unit_conflicts_prevented": conflicts,
            "reservations": list(self._reservations),
            "optimality_gap": gap,
        }
        if shortfall is not None:
            diagnostics["cash_shortfall"] = shortfall.to_json()
            diagnostics["infeasibility_reason"] = (
                "The household cash target cannot be met across the coordinated units "
                "within the shared gain budget and restrictions.")
            return Candidate(tuple(trades), "infeasible", diagnostics, self.name, self.version)

        return Candidate(tuple(trades), "feasible", diagnostics, self.name, self.version)

    def _optimality_gap(self, snapshot: Snapshot, units: Sequence[CoordinationUnit],
                        prices: Dict[str, str], budget, currency: str,
                        cash_by_tax_unit: Dict[str, Money], cash_target,
                        unit_diagnostics: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """How much cash any allocation of the same budget could have raised.

        Computed per tax unit, because each taxpayer holds its own budget.
        """
        excluded = hard_exclusions(unit_diagnostics)
        lots_by_tax_unit: Dict[str, List[BoundLot]] = {}
        for unit in units:
            for lot_id in unit.lot_ids:
                lot = snapshot.lot(lot_id)
                if lot is None or not lot.basis_known:
                    continue
                price = prices.get(lot.security_id)
                if price is None:
                    continue
                cash = Money.of(price).times(lot.quantity)
                gain = cash - lot.basis
                lots_by_tax_unit.setdefault(unit.tax_unit_id, []).append(
                    BoundLot(lot.lot_id, unit.unit_id, cash.amount, gain.amount))

        results = {
            tax_unit_id: relaxed_cash_bound(
                lots, budget,
                cash_by_tax_unit.get(tax_unit_id, Money.zero(currency)),
                excluded, currency, cash_target)
            for tax_unit_id, lots in sorted(lots_by_tax_unit.items())
        }
        return aggregate_bounds(results, currency)

    @staticmethod
    def _restrict(snapshot: Snapshot, unit: CoordinationUnit) -> Snapshot:
        """A view holding only this unit's lots.

        Accounts, transactions and coverage stay whole: wash-sale screening must
        still see the entire tax unit, which is the point of coordinating.
        """
        keep = set(unit.lot_ids)
        return replace(snapshot, lots=tuple(l for l in snapshot.lots if l.lot_id in keep))
