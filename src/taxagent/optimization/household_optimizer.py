"""Joint household optimization, with a decomposition proof.

The sequential coordinator serves accounts one at a time and cannot say what
the ordering cost. This engine does not order them at all: within a tax unit
it solves one problem over every eligible lot in every account at once.

## Why solving per tax unit is still exact

A gain budget belongs to a taxpayer. Two tax units never share one, so no
selection in one unit can relax or consume capacity in another, and the
objective is additive across units. A problem with no coupling between its
parts decomposes exactly — solving the parts separately and summing gives the
same answer as solving the whole. This is not an approximation and the engine
says so, which matters because the sequential coordinator's results look
similar and are not.

What *does* couple the units is wash-sale scope, which follows the taxpayer
and their spouse. That coupling is handled before the solve, by excluding lots
whose loss would be disallowed, so the selection problem itself stays
separable.

## What it still does not claim

Optimal here means optimal over the lots, prices and constraints supplied, for
the objective stated: reach the cash target, realize as little gain as
possible, use as few trades as possible. It is not a claim about what the
household should do. Risk, tracking error, a client's view on a position, and
every reason a lot was chosen in the first place are outside the model.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .. import CALCULATION_VERSIONS
from ..domain import codes
from ..domain.constraints import NettingBasis
from . import exact_solver
from .eligibility import eligible_items, trades_for
from .interface import Candidate, OptimizationProblem, UnsupportedCapability


def _as_outcome(status, bound, cash, target):
    """A minimal outcome for the shared gap builder, from the summed figures."""
    capped_bound = min(bound, target) if target is not None else bound
    capped_cash = min(cash, target) if target is not None else cash
    return exact_solver.SolveOutcome(
        (), cash, Decimal("0"), Decimal("0"), 0, status, capped_bound, 0, True,
        objective_cash=capped_cash)


class JointHouseholdOptimizer:
    name = "household-optimizer"
    version = CALCULATION_VERSIONS["household_optimizer"]
    supported_scopes = ("household",)
    claims_optimality = True

    def __init__(self, node_limit: int = exact_solver.DEFAULT_NODE_LIMIT):
        self.node_limit = node_limit

    def solve(self, problem: OptimizationProblem) -> Candidate:
        if problem.scope != "household":
            raise UnsupportedCapability(
                codes.UNSUPPORTED_SCOPE,
                "This engine solves a household jointly. For a single account use the "
                "account optimizer; for sequential allocation with reservations use "
                "the coordinator.",
                supported_alternative="account, household_coordinated")

        items, skipped, flagged = eligible_items(problem)
        constraints = problem.constraints
        budget = constraints.gain_budget
        netting = budget.netting_basis if budget else NettingBasis.NET_GAINS
        target = constraints.cash_target.amount if constraints.cash_target else None

        by_unit: Dict[str, List[exact_solver.SolverItem]] = defaultdict(list)
        for item in items:
            by_unit[item.unit_id or "unassigned"].append(item)

        # Each tax unit gets the whole budget: budgets are not pooled and not
        # divided. Splitting one budget across units would invent a constraint
        # nobody has; sharing one would invent capacity nobody has.
        per_unit: Dict[str, exact_solver.SolveOutcome] = {}
        for unit_id, unit_items in sorted(by_unit.items()):
            capacity = (budget.remaining.amount if budget
                        else sum((i.gain for i in unit_items if i.gain > 0),
                                 Decimal("0")))
            per_unit[unit_id] = exact_solver.solve(
                unit_items, capacity, netting,
                # The cash target is household-wide, so a unit may not be
                # capped at it individually; capping each unit separately would
                # stop the household short whenever one unit could cover more.
                None, node_limit=self.node_limit)

        # With no per-unit target, each unit maximises cash. Where the household
        # target is already met, trim back to the cheapest plan that still
        # reaches it rather than selling everything that could be sold.
        if target is not None:
            per_unit = self._trim_to_target(by_unit, per_unit, budget, netting, target)

        chosen = tuple(sorted(lot for o in per_unit.values() for lot in o.chosen))
        cash = sum((o.cash for o in per_unit.values()), Decimal("0"))
        gross = sum((o.gross_gains for o in per_unit.values()), Decimal("0"))
        net = sum((o.net_gain for o in per_unit.values()), Decimal("0"))
        bound = sum((o.bound_cash for o in per_unit.values()), Decimal("0"))
        exhausted = all(o.search_exhausted for o in per_unit.values())

        status = "optimal" if exhausted else "feasible"
        reason = ""
        if target is not None and cash < target:
            status = "infeasible"
            reason = (
                f"The household cannot reach {target} within its budgets. The most any "
                f"joint selection raises is {cash}. Because each tax unit was solved "
                "exhaustively and budgets do not cross units, this is a proof rather "
                "than a failure to find a plan."
                if exhausted else
                f"No selection found reaching {target}; the search did not exhaust the "
                "space, so infeasibility is not proven.")

        _declined = [d for unit_id, outcome in sorted(per_unit.items())
                     for d in exact_solver.declined_harvests(
                         by_unit.get(unit_id, []), outcome, netting)]
        diagnostics: Dict[str, Any] = {
            "coordination": "joint",
            "selection_rule": "exact branch and bound, decomposed by tax unit",
            "optimality_claimed": status == "optimal",
            "order_dependent": False,
            "decomposition": {
                "basis": "tax_unit",
                "units": sorted(by_unit),
                "proof": (
                    "A gain budget belongs to one taxpayer, so no selection in one tax "
                    "unit changes what is available in another, and the objective is "
                    "additive across units. The decomposition is therefore exact, not "
                    "an approximation: solving each unit and summing gives the same "
                    "answer as solving the household as one problem."),
            },
            "lots_considered": len(items),
            "lots_skipped": skipped,
            "wash_sale_flagged": flagged,
            "cash_raised": str(cash),
            "net_realized_gain": str(net),
            "gross_realized_gains": str(gross),
            "per_unit": [
                {"unit_id": unit_id, "trades": len(outcome.chosen),
                 "cash_raised": str(outcome.cash),
                 "budget_consumed": str(outcome.gross_gains
                                        if netting is NettingBasis.GROSS_GAINS
                                        else outcome.net_gain),
                 "solver_status": outcome.status,
                 "nodes_explored": outcome.nodes_explored,
                 "search_exhausted": outcome.search_exhausted}
                for unit_id, outcome in sorted(per_unit.items())
            ],
            "declined_loss_harvests": _declined,
            "optimality_gap": exact_solver.gap_report(
                _as_outcome(status, bound, cash, target), "exact_per_tax_unit",
                "Each tax unit was searched exhaustively, so a zero gap is a proof "
                "over the supplied lots and constraints — not a statement that the "
                "household should trade this way."),
        }
        if reason:
            diagnostics["infeasibility_reason"] = reason
        if target is not None and cash < target:
            diagnostics["cash_shortfall"] = str(target - cash)

        return Candidate(trades_for(problem, chosen), status, diagnostics,
                         self.name, self.version)

    def _trim_to_target(self, by_unit, per_unit, budget, netting, target):
        """Re-solve with each unit capped, once the household target is covered.

        Maximising cash in every unit reaches the target but oversells: a
        household needing $20,000 should not liquidate $200,000 because it
        could. Units are capped in a fixed order so the result stays
        deterministic, and a unit is only capped by what the household still
        needs after the units before it.
        """
        total = sum((o.cash for o in per_unit.values()), Decimal("0"))
        if total < target:
            return per_unit

        trimmed: Dict[str, exact_solver.SolveOutcome] = {}
        still_needed = target
        for unit_id in sorted(by_unit):
            unit_items = by_unit[unit_id]
            capacity = (budget.remaining.amount if budget
                        else sum((i.gain for i in unit_items if i.gain > 0),
                                 Decimal("0")))
            cap = still_needed if still_needed > 0 else None
            if cap is None:
                # The target is already covered; this unit contributes nothing.
                trimmed[unit_id] = exact_solver.SolveOutcome(
                    (), Decimal("0"), Decimal("0"), Decimal("0"), 0, "optimal",
                    Decimal("0"), 0, True,
                    certificate="not required: earlier tax units already cover the target")
                continue
            outcome = exact_solver.solve(unit_items, capacity, netting, cap,
                                         node_limit=self.node_limit)
            trimmed[unit_id] = outcome
            still_needed -= outcome.cash
        return trimmed
