"""The validator must be independent of the engine that produced the scenario."""
from datetime import date
from decimal import Decimal

from taxagent.decisions.constraint_compiler import compile_constraints
from taxagent.domain import codes
from taxagent.optimization.interface import Candidate, CandidateTrade
from taxagent.validation.result_validator import FAILED, INCOMPLETE, PASSED, validate_scenario

AS_OF = date(2026, 9, 30)


def _constraints(cash="1000.00", budget="15000.00"):
    intent = {
        "objectives": [{"type": "raise_cash", "amount": cash, "currency": "USD",
                        "withdrawal_account": "acct_tax"}],
        "constraints": [{"type": "realized_gain_budget", "amount": budget, "currency": "USD",
                         "netting_basis": "net_gains", "period": "2026"}],
    }
    return compile_constraints("tenant_ria_1", "acct_tax", intent)


def _candidate(*trades, status="feasible", **diagnostics):
    return Candidate(tuple(trades), status, diagnostics or {"claimed": "fine"},
                     "test-engine", "0.0.1")


def test_engine_self_report_does_not_override_recomputation(simple_snapshot):
    # The engine claims success while breaching the budget: the validator
    # recomputes from the snapshot and fails it anyway.
    trade = CandidateTrade("acct_tax", "VTI", "lot_gain", "100", "150.00")
    result = validate_scenario(simple_snapshot, _constraints(budget="100.00"),
                               _candidate(trade, status="optimal"), None, AS_OF)
    assert result.checks["gain_budget"] == FAILED
    assert result.violations[0].code == codes.INFEASIBLE_CONSTRAINTS
    assert result.passed is False


def test_overselling_a_lot_is_caught(simple_snapshot):
    trade = CandidateTrade("acct_tax", "VTI", "lot_gain", "5000", "150.00")
    result = validate_scenario(simple_snapshot, _constraints(), _candidate(trade), None, AS_OF)
    assert result.checks["position_conservation"] == FAILED


def test_trade_against_an_unknown_lot_is_caught(simple_snapshot):
    trade = CandidateTrade("acct_tax", "VTI", "lot_does_not_exist", "10", "150.00")
    result = validate_scenario(simple_snapshot, _constraints(), _candidate(trade), None, AS_OF)
    assert result.checks["position_conservation"] == FAILED


def test_mislabelled_account_on_a_trade_is_caught(simple_snapshot):
    trade = CandidateTrade("acct_roth", "VTI", "lot_gain", "10", "150.00")
    result = validate_scenario(simple_snapshot, _constraints(), _candidate(trade), None, AS_OF)
    assert result.checks["position_conservation"] == FAILED


def test_missing_screen_is_incomplete_not_passed(simple_snapshot):
    trade = CandidateTrade("acct_tax", "ARKK", "lot_loss", "100", "62.00")
    result = validate_scenario(simple_snapshot, _constraints(), _candidate(trade), None, AS_OF)
    assert result.checks["wash_sale_coverage"] == INCOMPLETE
    # Incomplete coverage is not a failure, and it is not a pass either.
    assert result.passed is True
    assert result.is_complete is False


def test_cash_shortfall_is_reported(simple_snapshot):
    trade = CandidateTrade("acct_tax", "ARKK", "lot_loss", "10", "62.00")
    result = validate_scenario(simple_snapshot, _constraints(cash="50000.00"),
                               _candidate(trade), None, AS_OF)
    assert result.checks["cash_target"] == FAILED
