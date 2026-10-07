"""The optimality gap bound: a ceiling on what coordination gives up."""
from datetime import date
from decimal import Decimal

import pytest

from taxagent.decisions.constraint_compiler import compile_constraints
from taxagent.domain import codes
from taxagent.domain.constraints import BudgetKind, GainBudget, NettingBasis
from taxagent.domain.coverage import CoverageInterval
from taxagent.domain.identities import Account, Registration
from taxagent.domain.money import Money
from taxagent.domain.tax_lots import BasisSource, make_lot
from taxagent.optimization.bounds import (BoundLot, hard_exclusions, relaxed_cash_bound)
from taxagent.optimization.harvest_selector import RuleBasedHarvestSelector
from taxagent.optimization.household_coordinator import SequentialHouseholdCoordinator
from taxagent.optimization.interface import OptimizationProblem
from taxagent.portfolio.snapshot_builder import build_snapshot

TENANT = "tenant_ria_1"
AS_OF = date(2026, 9, 30)


def _budget(amount="5000.00", netting=NettingBasis.GROSS_GAINS, external=None):
    return GainBudget(BudgetKind.REALIZED_GAIN, Money.of(amount), netting, "2026",
                      Money.of(external) if external else None)


# --- the bound function itself ------------------------------------------

def test_greedy_fills_budget_with_the_best_ratio_first():
    lots = [
        BoundLot("low", "u1", Decimal("6000"), Decimal("5000")),    # 1.2 cash per budget $
        BoundLot("high", "u2", Decimal("20000"), Decimal("5000")),  # 4.0
    ]
    result = relaxed_cash_bound(lots, _budget("5000.00"), Money.of("6000.00"))
    assert result.bound_cash.to_json() == "20000.00"
    assert result.gap.to_json() == "14000.00"
    assert result.gap_percent == "70.00%"
    assert result.is_provably_optimal is False


def test_zero_gap_proves_the_allocation_was_optimal():
    lots = [BoundLot("only", "u1", Decimal("20000"), Decimal("5000"))]
    result = relaxed_cash_bound(lots, _budget("5000.00"), Money.of("20000.00"))
    assert result.gap.is_zero
    assert result.is_provably_optimal is True


def test_bound_never_falls_below_what_was_achieved():
    lots = [BoundLot("a", "u1", Decimal("1000"), Decimal("500"))]
    # Even a nonsensical achieved figure cannot produce a negative gap.
    result = relaxed_cash_bound(lots, _budget("500.00"), Money.of("999999.00"))
    assert result.gap.is_zero


def test_budget_caps_the_fill():
    lots = [BoundLot("a", "u1", Decimal("20000"), Decimal("10000"))]
    result = relaxed_cash_bound(lots, _budget("5000.00"), Money.zero())
    # Half the budget buys half the lot under the divisibility relaxation.
    assert result.bound_cash.to_json() == "10000.00"


def test_losses_return_capacity_under_net_netting_only():
    lots = [
        BoundLot("loss", "u1", Decimal("4000"), Decimal("-3000")),
        BoundLot("gain", "u2", Decimal("12000"), Decimal("6000")),
    ]
    net = relaxed_cash_bound(lots, _budget("3000.00", NettingBasis.NET_GAINS), Money.zero())
    gross = relaxed_cash_bound(lots, _budget("3000.00", NettingBasis.GROSS_GAINS), Money.zero())
    # Net: 3,000 budget + 3,000 returned by the loss funds the whole gain lot.
    assert net.bound_cash.to_json() == "16000.00"
    # Gross: the loss raises cash but returns nothing, so only half the gain fits.
    assert gross.bound_cash.to_json() == "10000.00"


def test_bound_is_capped_at_the_cash_target():
    lots = [BoundLot("a", "u1", Decimal("50000"), Decimal("1000"))]
    result = relaxed_cash_bound(lots, _budget("5000.00"), Money.of("10000.00"),
                                cash_target=Money.of("10000.00"))
    # The target was met; no allocation was trying to raise more than that.
    assert result.bound_cash.to_json() == "10000.00"
    assert result.is_provably_optimal is True


def test_external_realized_reduces_capacity():
    lots = [BoundLot("a", "u1", Decimal("20000"), Decimal("5000"))]
    result = relaxed_cash_bound(
        lots, _budget("5000.00", external="4000.00"), Money.zero())
    # Only 1,000 of budget remains, so a fifth of the lot.
    assert result.bound_cash.to_json() == "4000.00"


def test_excluded_lots_do_not_inflate_the_bound():
    lots = [
        BoundLot("ok", "u1", Decimal("6000"), Decimal("5000")),
        BoundLot("restricted", "u2", Decimal("20000"), Decimal("5000")),
    ]
    result = relaxed_cash_bound(lots, _budget("5000.00"), Money.of("6000.00"),
                                excluded_lot_ids=["restricted"])
    assert result.bound_cash.to_json() == "6000.00"
    assert result.lots_excluded == 1
    assert result.is_provably_optimal is True


# --- which skips are reallocatable --------------------------------------

def test_hard_exclusions_keep_unsellable_lots_out():
    diagnostics = [{
        "lots_skipped": [
            {"lot_id": "no_basis", "reason": codes.MISSING_BASIS},
            {"lot_id": "restricted", "reason": codes.RESTRICTED_SECURITY},
            {"lot_id": "no_price", "reason": "PRICE_UNAVAILABLE"},
        ],
        "wash_sale_flagged": [{"lot_id": "washed", "reason": codes.WASH_SALE_RISK}],
    }]
    assert set(hard_exclusions(diagnostics)) == {"no_basis", "restricted", "no_price", "washed"}


def test_budget_exhausted_lots_remain_in_the_bound():
    # These are precisely the lots a different allocation might have chosen,
    # so excluding them would hide the gap the bound exists to measure.
    diagnostics = [{
        "lots_skipped": [
            {"lot_id": "starved", "reason": "GAIN_BUDGET_EXHAUSTED"},
            {"lot_id": "enough", "reason": "CASH_TARGET_MET"},
        ],
        "wash_sale_flagged": [],
    }]
    assert hard_exclusions(diagnostics) == []


# --- the gap the coordinator actually reports ---------------------------

def _two_account_snapshot():
    """Unit A is served first but converts budget to cash far less efficiently."""
    accounts = [
        Account("acct_a", TENANT, "tu_x", Registration.TAXABLE, "Schwab", "o"),
        Account("acct_b", TENANT, "tu_x", Registration.TAXABLE, "Fidelity", "o"),
    ]
    lots = [
        make_lot("lot_a", "acct_a", "AAA", "100", date(2020, 1, 1),
                 Money.of("1000.00"), BasisSource.CUSTODIAN_COVERED, True),
        make_lot("lot_b", "acct_b", "BBB", "100", date(2020, 1, 1),
                 Money.of("15000.00"), BasisSource.CUSTODIAN_COVERED, True),
    ]
    coverage = [CoverageInterval(a.account_id, date(2019, 1, 1), AS_OF, "src") for a in accounts]
    return build_snapshot(TENANT, "household_x", AS_OF, accounts, lots, [], coverage, ["fx"])


def _problem(snapshot, cash="30000.00", budget="5000.00", netting="gross_gains"):
    intent = {
        "objectives": [{"type": "raise_cash", "amount": cash, "currency": "USD",
                        "withdrawal_account": "acct_a"}],
        "constraints": [{"type": "realized_gain_budget", "amount": budget,
                         "currency": "USD", "netting_basis": netting, "period": "2026"}],
    }
    return OptimizationProblem(
        snapshot=snapshot, constraints=compile_constraints(TENANT, "household_x", intent),
        scope="household_coordinated", as_of=AS_OF,
        prices={"AAA": "60.00", "BBB": "200.00"})


def test_order_dependence_is_quantified(store):
    candidate = SequentialHouseholdCoordinator(
        RuleBasedHarvestSelector(), store, TENANT, "run_gap").solve(
        _problem(_two_account_snapshot()))

    gap = candidate.diagnostics["optimality_gap"]
    # Sequential served acct_a first and spent the whole budget for $6,000.
    assert gap["achieved_cash"] == "6000.00"
    # Spending the same budget on acct_b would have raised $20,000.
    assert gap["bound_cash"] == "20000.00"
    assert gap["gap"] == "14000.00"
    assert gap["provably_optimal"] is False
    assert gap["by_tax_unit"]["tu_x"]["gap"] == "14000.00"


def test_gap_reports_zero_when_nothing_could_be_reallocated(store, simple_snapshot):
    intent = {
        "objectives": [{"type": "raise_cash", "amount": "1000.00", "currency": "USD",
                        "withdrawal_account": "acct_tax"}],
        "constraints": [{"type": "realized_gain_budget", "amount": "15000.00",
                         "currency": "USD", "netting_basis": "net_gains", "period": "2026"}],
    }
    problem = OptimizationProblem(
        snapshot=simple_snapshot,
        constraints=compile_constraints(TENANT, "acct_tax", intent),
        scope="household_coordinated", as_of=date(2026, 9, 30),
        prices={"VTI": "150.00", "ARKK": "62.00"})
    candidate = SequentialHouseholdCoordinator(
        RuleBasedHarvestSelector(), store, TENANT, "run_ok").solve(problem)
    gap = candidate.diagnostics["optimality_gap"]
    # The cash target was met, so the bound is capped there and the gap closes.
    assert gap["provably_optimal"] is True
    assert gap["gap"] == "0.00"


def test_gap_carries_its_caveats(store):
    candidate = SequentialHouseholdCoordinator(
        RuleBasedHarvestSelector(), store, TENANT, "run_c").solve(
        _problem(_two_account_snapshot()))
    gap = candidate.diagnostics["optimality_gap"]
    assert gap["method"] == "lp_relaxation_fractional_knapsack"
    assert any("divisible" in c for c in gap["caveats"])
    assert "ceiling" in gap["interpretation"]


def test_budgets_are_bounded_per_tax_unit_not_pooled(store):
    snapshot = _two_account_snapshot()
    from dataclasses import replace
    # Move acct_b to its own taxpayer: each now holds a separate $5,000 budget.
    accounts = tuple(
        replace(a, tax_unit_id="tu_y") if a.account_id == "acct_b" else a
        for a in snapshot.accounts)
    split = replace(snapshot, accounts=accounts)
    candidate = SequentialHouseholdCoordinator(
        RuleBasedHarvestSelector(), store, TENANT, "run_split").solve(_problem(split))
    gap = candidate.diagnostics["optimality_gap"]
    assert set(gap["by_tax_unit"]) == {"tu_x", "tu_y"}
    # Each taxpayer's ceiling is computed against its own allowance.
    assert gap["by_tax_unit"]["tu_x"]["bound_cash"] == "6000.00"
    assert gap["by_tax_unit"]["tu_y"]["bound_cash"] == "20000.00"
