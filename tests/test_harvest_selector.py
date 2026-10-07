"""Reference tests named in capabilities/engines.yaml.

A capability claim without a passing reference test must not be advertised;
these are the tests that entitle the selector to be listed as implemented.
"""
from datetime import date
from decimal import Decimal

from taxagent.decisions.constraint_compiler import compile_constraints
from taxagent.domain import codes
from taxagent.domain.coverage import CoverageInterval
from taxagent.domain.identities import Account, Registration
from taxagent.domain.money import Money
from taxagent.domain.tax_lots import Action, BasisSource, Transaction, make_lot
from taxagent.optimization.harvest_selector import RuleBasedHarvestSelector
from taxagent.optimization.interface import OptimizationProblem
from taxagent.portfolio.snapshot_builder import build_snapshot

TENANT = "tenant_ria_1"
AS_OF = date(2026, 9, 30)
PRICES = {"VTI": "150.00", "ARKK": "62.00"}


def _problem(transactions=(), budget="15000.00", cash_target="1000.00",
             netting_basis="net_gains"):
    accounts = [
        Account("acct_tax", TENANT, "tu_1", Registration.TAXABLE, "Schwab", "o"),
        Account("acct_roth", TENANT, "tu_1", Registration.ROTH_IRA, "Fidelity", "o"),
    ]
    lots = [
        make_lot("lot_gain", "acct_tax", "VTI", "100", date(2020, 1, 10),
                 Money.of("10000.00"), BasisSource.CUSTODIAN_COVERED, True),   # +5,000
        make_lot("lot_loss", "acct_tax", "ARKK", "100", date(2025, 1, 10),
                 Money.of("12000.00"), BasisSource.CUSTODIAN_COVERED, True),   # -5,800
    ]
    coverage = [CoverageInterval(a.account_id, date(2019, 1, 1), AS_OF, "src") for a in accounts]
    snapshot = build_snapshot(TENANT, "acct_tax", AS_OF, accounts, lots, list(transactions),
                              coverage, ["fx"])
    intent = {
        "objectives": [{"type": "raise_cash", "amount": cash_target, "currency": "USD",
                        "withdrawal_account": "acct_tax"}],
        "constraints": [{"type": "realized_gain_budget", "amount": budget, "currency": "USD",
                         "netting_basis": netting_basis, "period": "2026"}],
    }
    constraints = compile_constraints(TENANT, "acct_tax", intent)
    return OptimizationProblem(snapshot, constraints, "account", AS_OF, PRICES)


def test_respects_gain_budget():
    # Gross netting: losses do not offset gains, so a 5,000 gain cannot fit a
    # 1,000 budget however much loss has been harvested.
    candidate = RuleBasedHarvestSelector().solve(
        _problem(budget="1000.00", cash_target="20000.00", netting_basis="gross_gains"))
    sold = {t.lot_id for t in candidate.trades}
    assert "lot_gain" not in sold
    assert any(s["reason"] == "GAIN_BUDGET_EXHAUSTED" for s in candidate.diagnostics["lots_skipped"])
    assert Decimal(candidate.diagnostics["gross_realized_gains"]) <= Decimal("1000.00")


def test_netting_basis_changes_the_answer():
    # The same request under net netting: the 5,800 harvested loss absorbs the
    # 5,000 gain, so the gain lot fits. This is why the compiler refuses to
    # guess a netting basis.
    candidate = RuleBasedHarvestSelector().solve(
        _problem(budget="1000.00", cash_target="20000.00", netting_basis="net_gains"))
    assert "lot_gain" in {t.lot_id for t in candidate.trades}
    assert Decimal(candidate.diagnostics["net_realized_gain"]) <= Decimal("1000.00")


def test_skips_lots_with_replacement_in_window():
    # Harvesting a loss that is already washed converts the benefit into a
    # disallowed loss, so the lot is excluded and the reason is reported.
    replacement = Transaction("t1", "acct_roth", "ARKK", Action.BUY, date(2026, 9, 20),
                              Decimal("100"), Money.of("6200.00"))
    candidate = RuleBasedHarvestSelector().solve(_problem(transactions=[replacement]))
    assert "lot_loss" not in {t.lot_id for t in candidate.trades}
    flagged = candidate.diagnostics["wash_sale_flagged"]
    assert flagged and flagged[0]["reason"] == codes.WASH_SALE_RISK


def test_never_claims_optimality():
    candidate = RuleBasedHarvestSelector().solve(_problem())
    assert candidate.solver_status != "optimal"
    assert candidate.diagnostics["optimality_claimed"] is False


def test_unreachable_cash_target_returns_infeasible_with_diagnostics():
    candidate = RuleBasedHarvestSelector().solve(_problem(budget="0.00", cash_target="90000.00"))
    assert candidate.solver_status == "infeasible"
    assert "cash_shortfall" in candidate.diagnostics
    # Constraints are never quietly relaxed to manufacture a result.
    assert "infeasibility_reason" in candidate.diagnostics


def test_missing_basis_lot_is_never_sold():
    problem = _problem()
    accounts = list(problem.snapshot.accounts)
    lots = list(problem.snapshot.lots) + [
        make_lot("lot_unknown", "acct_tax", "ARKK", "50", date(2024, 1, 1), None,
                 BasisSource.UNKNOWN, False)]
    snapshot = build_snapshot(TENANT, "acct_tax", AS_OF, accounts, lots, [],
                              list(problem.snapshot.coverage), ["fx"])
    candidate = RuleBasedHarvestSelector().solve(
        OptimizationProblem(snapshot, problem.constraints, "account", AS_OF, PRICES))
    assert "lot_unknown" not in {t.lot_id for t in candidate.trades}
