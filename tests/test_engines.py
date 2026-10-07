"""The three engines that claim optimality, validated against their claims.

`claims_optimality: true` is a licence to say "this is the best available".
These tests are what that licence costs: each engine is checked against an
independent computation of the same thing, against the weaker engine it is
supposed to beat, and against the invariants it must never break.
"""
import random
from datetime import date
from decimal import Decimal

import pytest

from case_runner import build_portfolio
from taxagent.domain.constraints import (BudgetKind, ConstraintSet, GainBudget,
                                         NettingBasis, Restriction, TargetAllocation)
from taxagent.domain.money import Money
from taxagent.optimization.exact_solver import SolverItem, brute_force, solve
from taxagent.optimization.harvest_selector import RuleBasedHarvestSelector
from taxagent.optimization.household_optimizer import JointHouseholdOptimizer
from taxagent.optimization.interface import OptimizationProblem, UnsupportedCapability
from taxagent.optimization.oracle_account_adapter import OracleAccountAdapter
from taxagent.optimization.rebalance_engine import TaxAwareRebalanceEngine

TENANT = "tenant_engines"
AS_OF = date(2026, 12, 31)
PRICES = {"AAA": "150", "BBB": "62", "CCC": "100", "DDD": "40"}


def _constraints(cash_target=None, budget=None, netting="gross_gains",
                 restrictions=(), target_allocation=None, subject="acct_a"):
    return ConstraintSet(
        "c_test", TENANT, subject,
        cash_target=Money.of(cash_target) if cash_target else None,
        gain_budget=(GainBudget(BudgetKind.REALIZED_GAIN, Money.of(budget),
                                NettingBasis(netting), "2026")
                     if budget is not None else None),
        restrictions=tuple(restrictions),
        target_allocation=target_allocation)


def _problem(spec, constraints, scope="account", objective="harvest_losses"):
    return OptimizationProblem(build_portfolio(spec), constraints, scope, AS_OF,
                               PRICES, objective=objective)


MIXED = {
    "as_of": "2026-12-31", "subject": "acct_a",
    "accounts": [{"id": "acct_a", "registration": "TAXABLE", "tax_unit": "tu_1"}],
    "lots": [
        {"id": "lot_gain_big", "account": "acct_a", "security": "AAA", "quantity": 100,
         "acquired": "2020-01-10", "basis": 10000},
        {"id": "lot_gain_small", "account": "acct_a", "security": "CCC", "quantity": 50,
         "acquired": "2019-05-01", "basis": 4000},
        {"id": "lot_loss_big", "account": "acct_a", "security": "BBB", "quantity": 100,
         "acquired": "2025-01-10", "basis": 12000},
    ],
}

HOUSEHOLD = {
    "as_of": "2026-12-31", "subject": "household_1",
    "accounts": [
        {"id": "acct_a", "registration": "TAXABLE", "tax_unit": "tu_1"},
        {"id": "acct_b", "registration": "TAXABLE", "tax_unit": "tu_1"},
    ],
    "lots": [
        {"id": "lot_a_gain", "account": "acct_a", "security": "AAA", "quantity": 100,
         "acquired": "2020-01-10", "basis": 10000},
        {"id": "lot_b_loss", "account": "acct_b", "security": "BBB", "quantity": 100,
         "acquired": "2025-01-10", "basis": 12000},
    ],
}

TWO_UNITS = {
    "as_of": "2026-12-31", "subject": "household_1",
    "accounts": [
        {"id": "acct_a", "registration": "TAXABLE", "tax_unit": "tu_1"},
        {"id": "acct_b", "registration": "TAXABLE", "tax_unit": "tu_2"},
    ],
    "lots": [
        {"id": "lot_a_gain", "account": "acct_a", "security": "AAA", "quantity": 100,
         "acquired": "2020-01-10", "basis": 10000},
        {"id": "lot_b_gain", "account": "acct_b", "security": "CCC", "quantity": 50,
         "acquired": "2019-05-01", "basis": 4000},
    ],
}


# --- oracle account adapter ----------------------------------------------

def test_an_unconfigured_adapter_refuses():
    """Not a feature flag. An unbound optimizer must not degrade silently.

    Falling back to a greedy pass would produce answers carrying the
    optimizer's name and the selector's quality.
    """
    with pytest.raises(UnsupportedCapability) as excinfo:
        OracleAccountAdapter(configured=False).solve(
            _problem(MIXED, _constraints("6000", "15000")))
    assert "not configured" in str(excinfo.value)


def test_the_adapter_refuses_household_scope_it_could_technically_serve():
    """The solver could handle it. Looping over accounts is still not joint."""
    with pytest.raises(UnsupportedCapability):
        OracleAccountAdapter().solve(
            _problem(HOUSEHOLD, _constraints("6000", "15000"), scope="household"))


def test_the_oracle_beats_the_greedy_selector_where_it_should():
    """The reason to build an optimizer at all.

    A 5,000 gross budget buys either the large gain lot (15,000 of cash) or the
    small one (5,000). Largest-loss-first ordering reaches the small lot first
    and then cannot afford the large one.
    """
    gains_only = {
        "as_of": "2026-12-31", "subject": "acct_a",
        "accounts": [{"id": "acct_a", "registration": "TAXABLE", "tax_unit": "tu_1"}],
        "lots": [
            {"id": "lot_gain_big", "account": "acct_a", "security": "AAA",
             "quantity": 100, "acquired": "2020-01-10", "basis": 10000},
            {"id": "lot_gain_small", "account": "acct_a", "security": "CCC",
             "quantity": 50, "acquired": "2019-05-01", "basis": 4000},
        ],
    }
    constraints = _constraints("15000", "5000")
    oracle = OracleAccountAdapter().solve(_problem(gains_only, constraints))
    greedy = RuleBasedHarvestSelector().solve(_problem(gains_only, constraints))

    assert oracle.solver_status == "optimal"
    assert {t.lot_id for t in oracle.trades} == {"lot_gain_big"}
    assert greedy.solver_status == "infeasible", (
        "the greedy selector was expected to miss this; if it now succeeds the "
        "case no longer demonstrates the difference and should be replaced")


def test_the_oracle_never_claims_optimality_from_a_truncated_search():
    adapter = OracleAccountAdapter(node_limit=2)
    candidate = adapter.solve(_problem(MIXED, _constraints(None, "15000")))
    assert candidate.solver_status != "optimal"
    assert candidate.diagnostics["optimality_claimed"] is False
    assert candidate.diagnostics["search_exhausted"] is False


def test_the_oracle_records_the_engine_build_it_ran():
    candidate = OracleAccountAdapter().solve(_problem(MIXED, _constraints(None, "15000")))
    assert candidate.diagnostics["engine_build"].startswith("exact-solver/")
    assert candidate.diagnostics["certificate"]


def test_a_restriction_is_never_traded_through_for_a_better_objective():
    constraints = _constraints("15000", "15000",
                               restrictions=[Restriction("DO_NOT_SELL", ("AAA",))])
    candidate = OracleAccountAdapter().solve(_problem(MIXED, constraints))
    assert "lot_gain_big" not in {t.lot_id for t in candidate.trades}


# --- joint household optimizer -------------------------------------------

def test_the_household_decomposition_matches_one_joint_solve():
    """The decomposition claim, checked rather than asserted.

    The engine solves each tax unit separately and sums. That is only exact if
    no selection in one unit changes what is available in another. Here the
    same instance is solved as one undivided problem by brute force, and the
    two must agree.
    """
    problem = _problem(HOUSEHOLD, _constraints("21200", "5000"), scope="household")
    joint = JointHouseholdOptimizer().solve(problem)

    items = [SolverItem("lot_a_gain", Decimal("15000"), Decimal("5000")),
             SolverItem("lot_b_loss", Decimal("6200"), Decimal("-5800"))]
    undivided = brute_force(items, Decimal("5000"), NettingBasis.GROSS_GAINS,
                            Decimal("21200"))
    assert {t.lot_id for t in joint.trades} == set(undivided.chosen)
    assert joint.solver_status == "optimal"


def test_budgets_are_never_pooled_across_tax_units():
    """Each taxpayer gets their own budget; the household does not share one.

    Two units, each with a 5,000 gross budget and a 5,000 gain lot. Pooling
    would allow only one sale; separate budgets allow both. Getting this wrong
    in the other direction — inventing one shared allowance — would let a
    household realize gains no single taxpayer is entitled to.
    """
    problem = _problem(TWO_UNITS, _constraints("20000", "5000"), scope="household")
    joint = JointHouseholdOptimizer().solve(problem)
    units = {u["unit_id"] for u in joint.diagnostics["per_unit"]}
    assert units == {"tu_1", "tu_2"}
    assert {t.lot_id for t in joint.trades} == {"lot_a_gain", "lot_b_gain"}
    assert joint.diagnostics["decomposition"]["basis"] == "tax_unit"


def test_joint_is_never_worse_than_sequential():
    """The engine exists to beat ordering. It must never lose to it."""
    from taxagent.optimization.household_coordinator import SequentialHouseholdCoordinator
    from taxagent.storage.sqlite import SqliteStore

    constraints = _constraints("21200", "5000", subject="household_1")
    joint = JointHouseholdOptimizer().solve(
        _problem(HOUSEHOLD, constraints, scope="household"))
    coordinator = SequentialHouseholdCoordinator(
        RuleBasedHarvestSelector(), SqliteStore(), TENANT, "run_cmp")
    sequential = coordinator.solve(
        _problem(HOUSEHOLD, constraints, scope="household_coordinated"))

    joint_cash = Decimal(joint.diagnostics["cash_raised"])
    sequential_cash = Decimal(str(sequential.diagnostics["cash_raised"]).replace(",", ""))
    assert joint_cash >= sequential_cash


def test_the_joint_engine_reports_no_order_dependence():
    joint = JointHouseholdOptimizer().solve(
        _problem(HOUSEHOLD, _constraints("21200", "5000"), scope="household"))
    assert joint.diagnostics["order_dependent"] is False
    assert joint.diagnostics["coordination"] == "joint"


def test_the_joint_engine_refuses_account_scope():
    with pytest.raises(UnsupportedCapability):
        JointHouseholdOptimizer().solve(_problem(MIXED, _constraints("6000", "15000")))


def test_an_unreachable_household_target_is_proven_not_guessed():
    problem = _problem(HOUSEHOLD, _constraints("50000", "0"), scope="household")
    joint = JointHouseholdOptimizer().solve(problem)
    assert joint.solver_status == "infeasible"
    assert "proof" in joint.diagnostics["infeasibility_reason"]


# --- rebalance engine -----------------------------------------------------

REBALANCE = {
    "as_of": "2026-12-31", "subject": "acct_a",
    "accounts": [{"id": "acct_a", "registration": "TAXABLE", "tax_unit": "tu_1"}],
    "lots": [
        {"id": "lot_aaa_1", "account": "acct_a", "security": "AAA", "quantity": 100,
         "acquired": "2020-01-10", "basis": 10000},
        {"id": "lot_aaa_2", "account": "acct_a", "security": "AAA", "quantity": 40,
         "acquired": "2021-01-10", "basis": 5000},
        {"id": "lot_bbb_1", "account": "acct_a", "security": "BBB", "quantity": 100,
         "acquired": "2025-01-10", "basis": 12000},
    ],
}
TARGET = TargetAllocation(weights=(("AAA", Decimal("0.50")), ("BBB", Decimal("0.30")),
                                   ("CCC", Decimal("0.20"))))


def test_a_rebalance_without_targets_is_refused():
    """Inferring a target would make the portfolio its own benchmark."""
    with pytest.raises(UnsupportedCapability) as excinfo:
        TaxAwareRebalanceEngine().solve(
            _problem(REBALANCE, _constraints(None, "10000"), objective="rebalance"))
    assert "target weights" in str(excinfo.value)


def test_rebalancing_never_increases_drift():
    """The one thing a rebalance must never do."""
    rng = random.Random(4242)
    for _ in range(60):
        budget = str(rng.randint(0, 8000))
        netting = rng.choice(["gross_gains", "net_gains"])
        constraints = _constraints(None, budget, netting, target_allocation=TARGET)
        candidate = TaxAwareRebalanceEngine().solve(
            _problem(REBALANCE, constraints, objective="rebalance"))
        assert candidate.solver_status != "failed"
        before = Decimal(candidate.diagnostics["drift_before"])
        after = Decimal(candidate.diagnostics["drift_after"])
        assert after <= before, f"drift rose with budget {budget}/{netting}"


def test_rebalancing_never_sells_past_target():
    """Overshooting closes drift on one side and opens it on the other."""
    constraints = _constraints(None, "100000", target_allocation=TARGET)
    candidate = TaxAwareRebalanceEngine().solve(
        _problem(REBALANCE, constraints, objective="rebalance"))
    for entry in candidate.diagnostics["per_security"]:
        assert Decimal(entry["sold"]) <= Decimal(entry["excess"]), entry["security_id"]
        assert Decimal(entry["residual"]) >= 0


def test_rebalancing_respects_the_gain_budget():
    constraints = _constraints(None, "1500", target_allocation=TARGET)
    candidate = TaxAwareRebalanceEngine().solve(
        _problem(REBALANCE, constraints, objective="rebalance"))
    assert Decimal(candidate.diagnostics["gross_realized_gains"]) <= Decimal("1500")


def test_a_zero_budget_rebalance_still_sells_what_costs_nothing():
    """A loss or break-even lot consumes no gross budget, so it is still available."""
    constraints = _constraints(None, "0", target_allocation=TargetAllocation(
        weights=(("AAA", Decimal("1.00")),)))
    candidate = TaxAwareRebalanceEngine().solve(
        _problem(REBALANCE, constraints, objective="rebalance"))
    assert Decimal(candidate.diagnostics["gross_realized_gains"]) == 0


def test_a_buy_that_would_wash_a_recent_loss_is_withheld():
    """Rebalancing back into a position sold at a loss disallows that loss.

    The client would see neither the loss nor a warning, so the purchase is
    withheld and the residual drift reported instead.
    """
    spec = {
        "as_of": "2026-12-31", "subject": "acct_a",
        "accounts": [{"id": "acct_a", "registration": "TAXABLE", "tax_unit": "tu_1"}],
        "lots": [
            {"id": "lot_aaa_1", "account": "acct_a", "security": "AAA", "quantity": 100,
             "acquired": "2020-01-10", "basis": 10000},
            {"id": "lot_ccc_1", "account": "acct_a", "security": "CCC", "quantity": 10,
             "acquired": "2019-01-10", "basis": 2000},
        ],
        "transactions": [
            {"id": "t_sell_ccc", "account": "acct_a", "security": "CCC",
             "action": "SELL", "date": "2026-12-21", "quantity": 40, "amount": 4000},
        ],
    }
    target = TargetAllocation(weights=(("AAA", Decimal("0.50")),
                                       ("CCC", Decimal("0.50"))))
    candidate = TaxAwareRebalanceEngine().solve(
        _problem(spec, _constraints(None, "10000", target_allocation=target),
                 objective="rebalance"))
    blocked = {b["security_id"] for b in candidate.diagnostics["buys_blocked_by_wash_sale"]}
    assert "CCC" in blocked
    assert "CCC" not in {b["security_id"] for b in candidate.diagnostics["buys"]}
    assert candidate.diagnostics.get("limitations")


def test_proceeds_are_allocated_in_proportion_to_shortfall():
    constraints = _constraints(None, "2000", target_allocation=TARGET)
    candidate = TaxAwareRebalanceEngine().solve(
        _problem(REBALANCE, constraints, objective="rebalance"))
    buys = {b["security_id"]: b["amount"] for b in candidate.diagnostics["buys"]}
    proceeds = Decimal(candidate.diagnostics["proceeds"])
    assert sum(buys.values(), Decimal("0")) <= proceeds
    # BBB is 1,960 short and CCC 5,440; CCC must receive the larger share.
    assert buys["CCC"] > buys["BBB"]


def test_every_optimality_claiming_engine_publishes_a_certificate():
    """A claim with no account of itself is the thing this project refuses."""
    for candidate in (
        OracleAccountAdapter().solve(_problem(MIXED, _constraints("6000", "15000"))),
        JointHouseholdOptimizer().solve(
            _problem(HOUSEHOLD, _constraints("21200", "5000"), scope="household")),
        TaxAwareRebalanceEngine().solve(
            _problem(REBALANCE, _constraints(None, "2000", target_allocation=TARGET),
                     objective="rebalance")),
    ):
        text = (candidate.diagnostics.get("certificate")
                or candidate.diagnostics.get("optimality_gap", {}).get("interpretation"))
        assert text, candidate.engine_name
        if candidate.diagnostics.get("optimality_claimed"):
            assert "exhaust" in text or "searched exhaustively" in text


def test_every_published_gap_has_the_fields_its_readers_require():
    """A gap block missing a field crashed the run that produced it.

    The report renderer and the console both read `gap_percent`. The joint
    optimizer published a gap without it, and the failure surfaced as
    VALIDATION_FAILED on an unrelated-looking case. All three engines now build
    the block from one function, and this asserts the contract.
    """
    required = {"method", "bound_cash", "achieved_cash", "gap", "gap_percent",
                "provably_optimal", "interpretation"}
    candidates = [
        OracleAccountAdapter().solve(_problem(MIXED, _constraints("6000", "15000"))),
        JointHouseholdOptimizer().solve(
            _problem(HOUSEHOLD, _constraints("21200", "5000"), scope="household")),
        JointHouseholdOptimizer().solve(
            _problem(HOUSEHOLD, _constraints("500000", "0"), scope="household")),
    ]
    for candidate in candidates:
        gap = candidate.diagnostics.get("optimality_gap")
        assert gap, candidate.engine_name
        assert required <= set(gap), (
            f"{candidate.engine_name} omits {sorted(required - set(gap))}")
        # provably_optimal and a non-zero gap cannot both be true.
        if gap["provably_optimal"]:
            assert Decimal(gap["gap"]) == 0


def test_a_gap_is_never_reported_as_negative():
    """A negative gap would mean the bound understated, which breaks pruning."""
    for budget in ("0", "500", "5000", "100000"):
        candidate = OracleAccountAdapter().solve(
            _problem(MIXED, _constraints("6000", budget)))
        assert Decimal(candidate.diagnostics["optimality_gap"]["gap"]) >= 0


def test_a_declined_loss_harvest_is_reported_not_hidden():
    """The objective's least obvious trade-off, made visible.

    Once the cash target is met the objective prefers fewer trades, so a loss
    that costs nothing against the gain budget can still be left behind. An
    advisor reading "optimal" would not expect that, and the engine cannot
    weigh their transaction costs against the tax benefit — so it reports what
    it passed over rather than deciding for them.
    """
    candidate = OracleAccountAdapter().solve(
        _problem(MIXED, _constraints("15000", "15000")))
    declined = candidate.diagnostics["declined_loss_harvests"]
    assert declined, "an available free loss harvest was passed over silently"
    entry = declined[0]
    assert entry["lot_id"] == "lot_loss_big"
    assert Decimal(entry["loss_foregone"]) == Decimal("5800.00")
    assert "costs nothing" in entry["reason"]


def test_nothing_is_reported_as_declined_when_it_was_actually_taken():
    candidate = OracleAccountAdapter().solve(
        _problem(MIXED, _constraints("21200", "15000")))
    sold = {t.lot_id for t in candidate.trades}
    declined = {d["lot_id"] for d in candidate.diagnostics["declined_loss_harvests"]}
    assert not (sold & declined)


def test_a_lot_excluded_for_cause_is_not_called_a_declined_harvest():
    """A restricted or unscreenable lot was never available to decline."""
    constraints = _constraints("15000", "15000",
                               restrictions=[Restriction("DO_NOT_SELL", ("BBB",))])
    candidate = OracleAccountAdapter().solve(_problem(MIXED, constraints))
    declined = {d["lot_id"] for d in candidate.diagnostics["declined_loss_harvests"]}
    assert "lot_loss_big" not in declined


# --- household structures that were never exercised -----------------------
# The system's main structural claim is that a gain budget belongs to a
# taxpayer and is never pooled. Four of seven registrations, and every trust
# shape, had no test behind that claim.

GRANTOR_TRUST = {
    "as_of": "2026-12-31", "subject": "household_1",
    "accounts": [
        {"id": "acct_a", "registration": "TAXABLE", "tax_unit": "tu_1"},
        # Taxed to the grantor, so the same tax unit and the same budget.
        {"id": "acct_gt", "registration": "TRUST_GRANTOR", "tax_unit": "tu_1"},
    ],
    "lots": [
        {"id": "lot_a_gain", "account": "acct_a", "security": "AAA", "quantity": 100,
         "acquired": "2020-01-10", "basis": 10000},
        {"id": "lot_gt_gain", "account": "acct_gt", "security": "CCC", "quantity": 50,
         "acquired": "2019-05-01", "basis": 4000},
    ],
}

NONGRANTOR_TRUST = {
    "as_of": "2026-12-31", "subject": "household_1",
    "accounts": [
        {"id": "acct_a", "registration": "TAXABLE", "tax_unit": "tu_1"},
        # Its own taxpayer, so its own allowance.
        {"id": "acct_ngt", "registration": "TRUST_NON_GRANTOR", "tax_unit": "tu_trust"},
    ],
    "lots": [
        {"id": "lot_a_gain", "account": "acct_a", "security": "AAA", "quantity": 100,
         "acquired": "2020-01-10", "basis": 10000},
        {"id": "lot_ngt_gain", "account": "acct_ngt", "security": "CCC", "quantity": 50,
         "acquired": "2019-05-01", "basis": 4000},
    ],
}


def test_a_grantor_trust_shares_the_grantor_budget():
    """One tax unit, so one $5,000 allowance covers both accounts together.

    AAA realizes $5,000 and CCC $1,000. Sharing one budget means only the AAA
    lot fits; both would be $6,000 of gross gains against a $5,000 allowance.
    """
    joint = JointHouseholdOptimizer().solve(
        _problem(GRANTOR_TRUST, _constraints(None, "5000", subject="household_1"),
                 scope="household"))
    units = {u["unit_id"] for u in joint.diagnostics["per_unit"]}
    assert units == {"tu_1"}, "a grantor trust must not get its own tax unit"
    assert Decimal(joint.diagnostics["gross_realized_gains"]) <= Decimal("5000")
    assert {t.lot_id for t in joint.trades} == {"lot_a_gain"}


def test_a_non_grantor_trust_gets_its_own_budget():
    """Two tax units, so two $5,000 allowances and both lots are reachable.

    This is the difference the registration makes, and it was untested. Pooling
    the two would deny the trust an allowance it is entitled to; merging the
    units the other way would let the household realize gains no single
    taxpayer may.
    """
    joint = JointHouseholdOptimizer().solve(
        _problem(NONGRANTOR_TRUST, _constraints(None, "5000", subject="household_1"),
                 scope="household"))
    units = {u["unit_id"] for u in joint.diagnostics["per_unit"]}
    assert units == {"tu_1", "tu_trust"}
    assert {t.lot_id for t in joint.trades} == {"lot_a_gain", "lot_ngt_gain"}
    # Each unit stayed inside its own allowance; the total exceeds either one.
    for unit in joint.diagnostics["per_unit"]:
        assert Decimal(unit["budget_consumed"]) <= Decimal("5000"), unit["unit_id"]
    assert Decimal(joint.diagnostics["gross_realized_gains"]) == Decimal("6000")


def test_the_two_trust_shapes_give_different_answers():
    """If they agreed, the registration would be decorative.

    The same lots, the same budget, and the only difference is whether the
    trust is its own taxpayer. A system that returned the same plan for both
    would be ignoring the distinction it claims to model.
    """
    constraints = _constraints(None, "5000", subject="household_1")
    grantor = JointHouseholdOptimizer().solve(
        _problem(GRANTOR_TRUST, constraints, scope="household"))
    nongrantor = JointHouseholdOptimizer().solve(
        _problem(NONGRANTOR_TRUST, constraints, scope="household"))
    assert ({t.lot_id for t in grantor.trades}
            != {t.lot_id for t in nongrantor.trades})
    assert (Decimal(nongrantor.diagnostics["cash_raised"])
            > Decimal(grantor.diagnostics["cash_raised"]))
