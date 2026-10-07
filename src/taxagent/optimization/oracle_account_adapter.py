"""Account-level optimizer, bound to the exact solver.

This is the adapter slot an external engine plugs into. It ships bound to the
in-tree exact solver, which is the reference build: it proves optimality where
it can exhaust the search and degrades to a reported gap where it cannot.

## Configured means validated, not merely present

`configured=False` is not a feature flag. An unbound adapter refuses, because
an optimizer that silently falls back to a greedy pass would produce answers
labelled with the optimizer's name and the selector's quality. Binding an
external engine means pinning a build identifier that the conformance suite
has run against; `engine_build` is recorded in evidence so a result can be
traced to the exact code that produced it.

## Scope stays account-level

Household scope raises UNSUPPORTED_SCOPE even though the solver could handle
it. Calling an account optimizer once per account is not joint optimization,
and an adapter must not manufacture a capability by looping.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from .. import CALCULATION_VERSIONS
from ..domain import codes
from ..domain.constraints import NettingBasis
from . import exact_solver
from .eligibility import eligible_items, trades_for
from .interface import Candidate, OptimizationProblem, UnsupportedCapability

REFERENCE_BUILD = "exact-solver/" + CALCULATION_VERSIONS.get("exact_solver", "1.0.0")


class OracleAccountAdapter:
    name = "oracle-account-adapter"
    version = CALCULATION_VERSIONS.get("oracle_adapter", "1.0.0")
    supported_scopes = ("account",)
    claims_optimality = True

    def __init__(self, configured: bool = True, engine_build: str = REFERENCE_BUILD,
                 node_limit: int = exact_solver.DEFAULT_NODE_LIMIT):
        self.configured = configured
        self.engine_build = engine_build
        self.node_limit = node_limit

    def solve(self, problem: OptimizationProblem) -> Candidate:
        if problem.scope != "account":
            raise UnsupportedCapability(
                codes.UNSUPPORTED_SCOPE,
                "This engine optimizes one account at a time. Household-level "
                "optimization is not available here; per-account results must not be "
                "described as household-optimal.",
                supported_alternative="household-optimizer for a joint household run",
            )
        if not self.configured:
            raise UnsupportedCapability(
                codes.UNSUPPORTED_SCOPE,
                "The external optimizer is not configured or validated in this "
                "deployment.",
                supported_alternative="rule-based-harvest-selector",
            )

        items, skipped, flagged = eligible_items(problem)
        constraints = problem.constraints
        budget = constraints.gain_budget
        netting = budget.netting_basis if budget else NettingBasis.NET_GAINS
        capacity = (budget.remaining.amount if budget
                    else sum((i.gain for i in items if i.gain > 0), Decimal("0")))
        target = constraints.cash_target.amount if constraints.cash_target else None

        outcome = exact_solver.solve(items, capacity, netting, target,
                                     node_limit=self.node_limit)

        _declined = exact_solver.declined_harvests(items, outcome, netting)
        diagnostics: Dict[str, Any] = {
            "selection_rule": "exact branch and bound over eligible lots",
            # Only ever true when the search provably exhausted the space.
            "optimality_claimed": outcome.status == "optimal",
            "engine_build": self.engine_build,
            "lots_considered": len(items),
            "lots_skipped": skipped,
            "wash_sale_flagged": flagged,
            "cash_raised": str(outcome.cash),
            "net_realized_gain": str(outcome.net_gain),
            "gross_realized_gains": str(outcome.gross_gains),
            "nodes_explored": outcome.nodes_explored,
            "search_exhausted": outcome.search_exhausted,
            "certificate": outcome.certificate,
            "declined_loss_harvests": _declined,
            "optimality_gap": exact_solver.gap_report(
                outcome, "lp_relaxation_fractional_knapsack",
                "Zero gap with an exhausted search is a proof: no selection of these "
                "lots raises more cash within this budget. A gap is a ceiling from a "
                "relaxed problem, not a reachable improvement."),
        }
        if outcome.reason:
            diagnostics["infeasibility_reason"] = outcome.reason
        if target is not None and outcome.cash < target:
            diagnostics["cash_shortfall"] = str(target - outcome.cash)

        return Candidate(trades_for(problem, outcome.chosen), outcome.status,
                         diagnostics, self.name, self.version)
