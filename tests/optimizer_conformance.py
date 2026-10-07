"""Conformance runner for optimizer adapters.

Every case is data. It builds a synthetic portfolio, asks an adapter to solve
it, and then checks two things:

1. **Invariants**, applied to every case regardless of what it was written for.
   These are recomputed from the snapshot by `validate_scenario`, so an adapter
   cannot satisfy them by reporting that it did.
2. **The case's own expectations**, which pin the specific behaviour it exists
   to describe.

A case may declare `requires: [optimality]`. An adapter that does not claim
optimality skips those rather than failing them: a greedy selector is not
wrong for missing an optimum it never promised. Those skipped cases are
exactly the gate a real optimizer has to clear.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import yaml

from taxagent.application.bootstrap import PROJECT_ROOT
from taxagent.domain.constraints import (BudgetKind, ConstraintSet, GainBudget,
                                         NettingBasis, Restriction, TargetAllocation)
from taxagent.domain.money import Money
from taxagent.optimization.harvest_selector import RuleBasedHarvestSelector
from taxagent.optimization.household_coordinator import SequentialHouseholdCoordinator
from taxagent.optimization.interface import OptimizationProblem, UnsupportedCapability
from taxagent.optimization.household_optimizer import JointHouseholdOptimizer
from taxagent.optimization.rebalance_engine import TaxAwareRebalanceEngine
from taxagent.optimization.oracle_account_adapter import OracleAccountAdapter
from taxagent.domain.sleeves import PlannedAcquisition
from taxagent.validation.result_validator import validate_scenario

from case_runner import build_portfolio          # synthetic portfolios, shared

CASES_DIR = os.path.join(PROJECT_ROOT, "tests", "cases", "optimizer")
TENANT = "tenant_cases"


@dataclass(frozen=True)
class Adapter:
    name: str
    build: Any
    claims_optimality: bool
    scopes: Tuple[str, ...]
    #: Which objective this engine optimises. A rebalancer handed a harvest
    #: case would be judged against an objective it does not pursue, and a
    #: harvest engine handed a rebalance would optimise for the wrong thing —
    #: which is the error this whole separation exists to prevent.
    objective: str = "harvest_losses"

    def capabilities(self) -> set:
        caps = {"feasibility"}
        if self.claims_optimality:
            caps.add("optimality")
        return caps


def _coordinator():
    from taxagent.storage.sqlite import SqliteStore
    return SequentialHouseholdCoordinator(RuleBasedHarvestSelector(), SqliteStore(),
                                          TENANT, "run_conformance")


#: Adapters under test. Registering a new optimizer means adding a line here
#: and watching the suite go green, including the optimality cases.
ADAPTERS: Dict[str, Adapter] = {
    "rule-based-harvest-selector": Adapter(
        "rule-based-harvest-selector", RuleBasedHarvestSelector, False, ("account",)),
    "household-coordinator": Adapter(
        "household-coordinator", _coordinator, False, ("household_coordinated",)),
    # Bound to the in-tree exact solver and therefore claiming optimality,
    # which un-gates every case marked `requires: [optimality]`.
    "oracle-account-adapter": Adapter(
        "oracle-account-adapter", lambda: OracleAccountAdapter(configured=True),
        True, ("account",)),
    # The same adapter unbound, to prove that an unconfigured engine refuses
    # rather than quietly degrading to a greedy pass under the optimizer's name.
    "oracle-account-adapter(unconfigured)": Adapter(
        "oracle-account-adapter(unconfigured)",
        lambda: OracleAccountAdapter(configured=False), False, ("account",)),
    # Joint across a household, decomposed exactly by tax unit.
    "household-optimizer": Adapter(
        "household-optimizer", JointHouseholdOptimizer, True, ("household",)),
    # Rebalancing optimises a different objective, so it has its own cases.
    "rebalance-engine": Adapter(
        "rebalance-engine", TaxAwareRebalanceEngine, True, ("account",),
        objective="rebalance"),
}


def load_portfolios() -> Dict[str, Any]:
    """Shared synthetic portfolios, so 100+ cases need not restate them."""
    path = os.path.join(CASES_DIR, "_portfolios.yaml")
    return yaml.safe_load(open(path, encoding="utf-8")) if os.path.exists(path) else {}


PORTFOLIOS = load_portfolios()


def portfolio_for(case: Dict[str, Any]) -> Dict[str, Any]:
    if "portfolio" in case:
        return case["portfolio"]
    name = case["portfolio_ref"]
    if name not in PORTFOLIOS["portfolios"]:
        raise KeyError(f"{case['id']} references unknown portfolio {name!r}")
    return PORTFOLIOS["portfolios"][name]


def prices_for(case: Dict[str, Any]) -> Dict[str, str]:
    prices = dict(PORTFOLIOS.get("prices", {}))
    prices.update(case.get("prices", {}))
    return {k: str(v) for k, v in prices.items()}


def load_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(CASES_DIR)):
        if not name.endswith((".yaml", ".yml")) or name.startswith("_"):
            continue
        loaded = yaml.safe_load(
            open(os.path.join(CASES_DIR, name), encoding="utf-8")) or []
        for case in loaded:
            if case.get("_template"):        # anchor holders, not cases
                continue
            case["_file"] = name
            cases.append(case)
    return cases


# --- problem construction -------------------------------------------------

def build_constraints(case: Dict[str, Any]) -> ConstraintSet:  # noqa: C901
    spec = case.get("constraints") or {}
    budget = None
    if "gain_budget" in spec:
        raw = spec["gain_budget"]
        budget = GainBudget(
            kind=BudgetKind.REALIZED_GAIN, amount=Money.of(str(raw["amount"])),
            netting_basis=NettingBasis(raw.get("netting", "net_gains")),
            period=str(raw.get("period", "2026")),
            external_realized=(Money.of(str(raw["external_realized"]))
                               if raw.get("external_realized") is not None else None),
            hard=raw.get("hard", True))
    restrictions = tuple(
        Restriction(code=r.get("code", "RESTRICTED"),
                    security_ids=tuple(r.get("securities", ())),
                    account_ids=tuple(r.get("accounts", ())),
                    min_holding_days=r.get("min_holding_days"),
                    note=r.get("note", ""))
        for r in spec.get("restrictions", []))
    return ConstraintSet(
        constraints_ref="constraints_case", tenant_id=TENANT,
        subject_id=portfolio_for(case).get("subject", "subject_1"),
        cash_target=(Money.of(str(spec["cash_target"]))
                     if spec.get("cash_target") is not None else None),
        withdrawal_account_id=spec.get("withdrawal_account"),
        gain_budget=budget, restrictions=restrictions,
        target_allocation=(
            TargetAllocation(
                weights=tuple((str(s), Decimal(str(w)))
                              for s, w in spec["target_allocation"]["weights"].items()),
                tolerance=Decimal(str(spec["target_allocation"].get("tolerance", "0"))))
            if "target_allocation" in spec else None))


def build_problem(case: Dict[str, Any], scope: str) -> OptimizationProblem:
    snapshot = build_portfolio(portfolio_for(case))
    planned = tuple(
        PlannedAcquisition(p["account"], p["security"], Decimal(str(p["quantity"])),
                           date.fromisoformat(p["date"]), p.get("unit", ""))
        for p in case.get("planned_purchases", []))
    return OptimizationProblem(
        snapshot=snapshot, constraints=build_constraints(case), scope=scope,
        as_of=snapshot.as_of,
        prices=prices_for(case),
        objective=case.get("objective", "harvest_losses"),
        planned_acquisitions=planned)


# --- checks ---------------------------------------------------------------

def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def check_invariants(problem: OptimizationProblem, candidate) -> List[str]:
    """Applied to every case. Recomputed from the snapshot, never trusted."""
    failures: List[str] = []
    validation = validate_scenario(problem.snapshot, problem.constraints, candidate,
                                   None, problem.as_of)

    if candidate.solver_status != "infeasible":
        for violation in validation.violations:
            # An infeasible result is allowed to miss the cash target; nothing
            # is allowed to breach a hard budget or oversell a lot.
            if violation.check != "cash_target":
                failures.append(f"invariant {violation.check}: {violation.message}")

    for trade in candidate.trades:
        lot = problem.snapshot.lot(trade.lot_id)
        if lot is None:
            failures.append(f"invariant lot_exists: {trade.lot_id} is not in the snapshot")
            continue
        if not lot.basis_known:
            failures.append(f"invariant basis_known: sold {trade.lot_id} with unknown basis")
        if lot.acquisition_date is None:
            failures.append(f"invariant acquisition_date: sold {trade.lot_id} with no date")
        if _decimal(trade.quantity) <= 0:
            failures.append(f"invariant positive_quantity: {trade.lot_id}")

    sold = [t.lot_id for t in candidate.trades]
    if len(sold) != len(set(sold)):
        failures.append("invariant unique_lots: a lot appears twice in the trade list")

    if candidate.solver_status not in {"optimal", "feasible", "infeasible", "failed"}:
        failures.append(f"invariant solver_status: {candidate.solver_status!r} is not a status")

    if candidate.solver_status == "optimal" and not candidate.diagnostics.get(
            "optimality_claimed", False):
        failures.append("invariant optimality: reported optimal without claiming it")

    return failures


def check_expectations(case: Dict[str, Any], problem: OptimizationProblem,
                       candidate) -> List[str]:
    expect = case.get("expect") or {}
    failures: List[str] = []
    sold = {t.lot_id for t in candidate.trades}
    diagnostics = candidate.diagnostics or {}

    if "solver_status" in expect and candidate.solver_status != expect["solver_status"]:
        failures.append(
            f"solver_status: expected {expect['solver_status']}, got {candidate.solver_status}")
    for lot_id in expect.get("sells_include", []):
        if lot_id not in sold:
            failures.append(f"sells_include: {lot_id} was not sold")
    for lot_id in expect.get("sells_exclude", []):
        if lot_id in sold:
            failures.append(f"sells_exclude: {lot_id} was sold")
    if "sells_exactly" in expect and sold != set(expect["sells_exactly"]):
        failures.append(f"sells_exactly: expected {sorted(expect['sells_exactly'])}, "
                        f"got {sorted(sold)}")
    if "trade_count" in expect and len(candidate.trades) != expect["trade_count"]:
        failures.append(f"trade_count: expected {expect['trade_count']}, "
                        f"got {len(candidate.trades)}")

    recomputed = validate_scenario(problem.snapshot, problem.constraints, candidate,
                                   None, problem.as_of).recomputed["totals"]
    numeric = {
        "cash_at_least": ("total_proceeds", lambda a, e: a >= e),
        "cash_at_most": ("total_proceeds", lambda a, e: a <= e),
        "cash_equals": ("total_proceeds", lambda a, e: a == e),
        "net_gain_at_most": ("net_gain", lambda a, e: a <= e),
        "net_gain_at_least": ("net_gain", lambda a, e: a >= e),
        "gross_gains_at_most": ("gross_gains", lambda a, e: a <= e),
        "losses_at_most": ("gross_losses", lambda a, e: a <= e),
    }
    for key, (field, compare) in numeric.items():
        if key in expect:
            actual = _decimal(recomputed[field])
            if not compare(actual, _decimal(expect[key])):
                failures.append(f"{key}: expected {expect[key]}, got {actual}")

    for key in expect.get("diagnostics_include", []):
        if key not in diagnostics:
            failures.append(f"diagnostics_include: {key} is absent")
    for needle in expect.get("diagnostics_mention", []):
        if needle not in str(diagnostics):
            failures.append(f"diagnostics_mention: {needle!r} not found")

    # --- rebalance-specific ------------------------------------------------
    if expect.get("drift_decreases"):
        before = _decimal(diagnostics.get("drift_before", "0"))
        after = _decimal(diagnostics.get("drift_after", "0"))
        if after >= before:
            failures.append(f"drift_decreases: {before} -> {after}")
    if expect.get("never_sells_past_target"):
        for entry in diagnostics.get("per_security", []):
            if _decimal(entry["sold"]) > _decimal(entry["excess"]):
                failures.append(
                    f"never_sells_past_target: {entry['security_id']} sold "
                    f"{entry['sold']} against an excess of {entry['excess']}")

    return failures


#: Every expectation key the checker implements. A case naming anything else
#: is asserting nothing, which is worse than having no case at all — it reads
#: as coverage. Two rebalance cases were doing exactly that until this guard
#: was added.
KNOWN_EXPECTATIONS = frozenset({
    "solver_status", "sells_include", "sells_exclude", "sells_exactly",
    "trade_count", "cash_at_least", "cash_at_most", "cash_equals",
    "net_gain_at_most", "net_gain_at_least", "gross_gains_at_most",
    "losses_at_most", "diagnostics_include", "diagnostics_mention",
    "refused", "refusal_code", "drift_decreases", "never_sells_past_target",
})


def run(case: Dict[str, Any], adapter: Adapter) -> List[str]:
    scope = case.get("scope", "account")
    problem = build_problem(case, scope)
    expect = case.get("expect") or {}

    try:
        candidate = adapter.build().solve(problem)
    except UnsupportedCapability as exc:
        if expect.get("refused"):
            code = expect.get("refusal_code")
            return [] if (code is None or code == exc.code) else [
                f"refusal_code: expected {code}, got {exc.code}"]
        return [f"unexpectedly refused: {exc}"]

    if expect.get("refused"):
        return ["expected a refusal, but the adapter produced a candidate"]

    return check_invariants(problem, candidate) + check_expectations(case, problem, candidate)


def run_twice(case: Dict[str, Any], adapter: Adapter) -> Tuple[List[str], List[str]]:
    """Two independent solves of the same case, for determinism checks."""
    scope = case.get("scope", "account")
    trades = []
    for _ in range(2):
        problem = build_problem(case, scope)
        candidate = adapter.build().solve(problem)
        trades.append([(t.lot_id, t.quantity, t.price_per_share)
                       for t in candidate.trades])
    return trades[0], trades[1]
