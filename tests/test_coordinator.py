"""Reference tests for the household / UMA coordinator.

Named in capabilities/engines.yaml. The engine may not advertise
`household_coordinated` support unless these pass.
"""
from datetime import date
from decimal import Decimal

import pytest

from taxagent.decisions.constraint_compiler import compile_constraints
from taxagent.domain import codes
from taxagent.domain.coverage import CoverageInterval
from taxagent.domain.identities import Account, Registration
from taxagent.domain.money import Money
from taxagent.domain.sleeves import PlannedAcquisition, UnitKind
from taxagent.domain.tax_lots import Action, BasisSource, Transaction, make_lot
from taxagent.optimization.harvest_selector import RuleBasedHarvestSelector
from taxagent.optimization.household_coordinator import (SequentialHouseholdCoordinator,
                                                         derive_units)
from taxagent.optimization.interface import BudgetUnavailable, OptimizationProblem
from taxagent.portfolio.snapshot_builder import build_snapshot
from taxagent.domain.sleeves import Sleeve

TENANT = "tenant_ria_1"
AS_OF = date(2026, 9, 30)
PRICES = {"VTI": "150.00", "ARKK": "62.00", "AAPL": "205.00"}


def _uma_snapshot(extra_transactions=(), second_tax_unit=False):
    """One UMA account, two sleeves under different managers."""
    accounts = [
        Account("acct_uma", TENANT, "tu_patel", Registration.TAXABLE, "Schwab", "owner_patel"),
    ]
    if second_tax_unit:
        accounts.append(
            Account("acct_trust", TENANT, "tu_trust", Registration.TRUST_NON_GRANTOR,
                    "Schwab", "trustee"))
    sleeves = [
        Sleeve("sleeve_alpha", "acct_uma", "manager_alpha", "model_lgc", "0.5"),
        Sleeve("sleeve_beta", "acct_uma", "manager_beta", "model_core", "0.5"),
    ]
    lots = [
        # Alpha: a loss in ARKK and a gain in VTI
        make_lot("lot_a_arkk", "acct_uma", "ARKK", "100", date(2025, 1, 10),
                 Money.of("12000.00"), BasisSource.CUSTODIAN_COVERED, True),
        make_lot("lot_a_vti", "acct_uma", "VTI", "100", date(2020, 1, 10),
                 Money.of("10000.00"), BasisSource.CUSTODIAN_COVERED, True),
        # Beta: a gain in AAPL
        make_lot("lot_b_aapl", "acct_uma", "AAPL", "100", date(2019, 2, 20),
                 Money.of("8000.00"), BasisSource.CUSTODIAN_COVERED, True),
    ]
    assignments = [("lot_a_arkk", "sleeve_alpha"), ("lot_a_vti", "sleeve_alpha"),
                   ("lot_b_aapl", "sleeve_beta")]
    if second_tax_unit:
        lots.append(make_lot("lot_t_vti", "acct_trust", "VTI", "100", date(2020, 1, 10),
                             Money.of("10000.00"), BasisSource.CUSTODIAN_COVERED, True))
    coverage = [CoverageInterval(a.account_id, date(2019, 1, 1), AS_OF, "src") for a in accounts]
    return build_snapshot(TENANT, "household_patel", AS_OF, accounts, lots,
                          list(extra_transactions), coverage, ["fx"],
                          sleeves=sleeves, sleeve_assignments=assignments)


def _constraints(cash="1000.00", budget="15000.00", netting="net_gains"):
    intent = {
        "objectives": [{"type": "raise_cash", "amount": cash, "currency": "USD",
                        "withdrawal_account": "acct_uma"}],
        "constraints": [{"type": "realized_gain_budget", "amount": budget, "currency": "USD",
                         "netting_basis": netting, "period": "2026"}],
    }
    return compile_constraints(TENANT, "household_patel", intent)


def _problem(snapshot, constraints, planned=()):
    return OptimizationProblem(
        snapshot=snapshot, constraints=constraints, scope="household_coordinated",
        as_of=AS_OF, prices=PRICES, units=(), planned_acquisitions=tuple(planned))


# --- reference tests -----------------------------------------------------

def test_shared_budget_is_not_double_spent(store):
    # Gross netting so losses do not create room: a 6,000 budget cannot cover
    # Alpha's 5,000 VTI gain and Beta's 12,500 AAPL gain together.
    snapshot = _uma_snapshot()
    coordinator = SequentialHouseholdCoordinator(
        RuleBasedHarvestSelector(), store, TENANT, "run_1")
    candidate = coordinator.solve(
        _problem(snapshot, _constraints(cash="40000.00", budget="6000.00",
                                        netting="gross_gains")))

    consumed = Decimal(candidate.diagnostics["budget_consumed_by_tax_unit"]["tu_patel"])
    assert consumed <= Decimal("6000.00")
    # The second unit saw a budget already reduced by the first.
    per_unit = {u["unit_id"]: u for u in candidate.diagnostics["per_unit"]}
    assert sum(Decimal(u["budget_consumed"]) for u in per_unit.values()) == consumed


def test_sleeve_cannot_wash_another_sleeves_loss(store):
    # Alpha wants to harvest the ARKK loss; Beta's model intends to buy ARKK
    # inside the window. Same account, same taxpayer: no internal netting saves
    # it, so the overlay must prevent the harvest.
    snapshot = _uma_snapshot()
    planned = [PlannedAcquisition("acct_uma", "ARKK", Decimal("80"),
                                  date(2026, 10, 10), "sleeve_beta")]
    coordinator = SequentialHouseholdCoordinator(
        RuleBasedHarvestSelector(), store, TENANT, "run_2")
    candidate = coordinator.solve(_problem(snapshot, _constraints(), planned))

    assert "lot_a_arkk" not in {t.lot_id for t in candidate.trades}
    conflicts = candidate.diagnostics["cross_unit_conflicts_prevented"]
    assert conflicts, "the cross-sleeve conflict was not detected"
    assert conflicts[0]["reason"] == codes.CROSS_UNIT_WASH_CONFLICT
    assert conflicts[0]["selling_unit"] == "sleeve_alpha"
    assert "sleeve_beta" in conflicts[0]["conflicting_units"]


def test_allocation_order_is_recorded(store):
    snapshot = _uma_snapshot()
    candidate = SequentialHouseholdCoordinator(
        RuleBasedHarvestSelector(), store, TENANT, "run_3").solve(
        _problem(snapshot, _constraints()))
    diagnostics = candidate.diagnostics
    assert diagnostics["coordination"] == "sequential"
    assert diagnostics["order_dependent"] is True
    assert diagnostics["order_rule"]
    # Alpha holds the larger unrealized loss, so it is served first.
    assert diagnostics["allocation_order"][0] == "sleeve_alpha"


# --- further behaviour ---------------------------------------------------

def test_coordinator_never_claims_optimality(store):
    candidate = SequentialHouseholdCoordinator(
        RuleBasedHarvestSelector(), store, TENANT, "run_4").solve(
        _problem(_uma_snapshot(), _constraints()))
    assert candidate.solver_status != "optimal"
    assert candidate.diagnostics["optimality_claimed"] is False


def test_budget_is_per_tax_unit_not_per_household(store):
    # The trust is a separate taxpayer in the same household: it gets its own
    # allowance rather than sharing the joint account's.
    snapshot = _uma_snapshot(second_tax_unit=True)
    candidate = SequentialHouseholdCoordinator(
        RuleBasedHarvestSelector(), store, TENANT, "run_5").solve(
        _problem(snapshot, _constraints(cash="60000.00", budget="6000.00",
                                        netting="gross_gains")))
    consumed = candidate.diagnostics["budget_consumed_by_tax_unit"]
    assert set(consumed) == {"tu_patel", "tu_trust"}
    for amount in consumed.values():
        assert Decimal(amount) <= Decimal("6000.00")


def test_concurrent_run_cannot_spend_reserved_capacity(store):
    snapshot = _uma_snapshot()
    constraints = _constraints(cash="40000.00", budget="6000.00", netting="gross_gains")
    first = SequentialHouseholdCoordinator(RuleBasedHarvestSelector(), store, TENANT, "run_a")
    first.solve(_problem(snapshot, constraints))

    second = SequentialHouseholdCoordinator(RuleBasedHarvestSelector(), store, TENANT, "run_b")
    with pytest.raises(BudgetUnavailable) as excinfo:
        second.solve(_problem(snapshot, constraints))
    assert excinfo.value.code == codes.BUDGET_RESERVED_ELSEWHERE


def test_releasing_reservations_frees_the_budget(store):
    snapshot = _uma_snapshot()
    constraints = _constraints(cash="40000.00", budget="6000.00", netting="gross_gains")
    first = SequentialHouseholdCoordinator(RuleBasedHarvestSelector(), store, TENANT, "run_a")
    first.solve(_problem(snapshot, constraints))
    first.release_all()

    second = SequentialHouseholdCoordinator(RuleBasedHarvestSelector(), store, TENANT, "run_b")
    candidate = second.solve(_problem(snapshot, constraints))
    assert candidate.solver_status in {"feasible", "infeasible"}


def test_units_derive_from_sleeves_when_present():
    units = derive_units(_uma_snapshot())
    by_id = {u.unit_id: u for u in units}
    assert by_id["sleeve_alpha"].kind is UnitKind.SLEEVE
    assert by_id["sleeve_alpha"].manager_ref == "manager_alpha"
    assert set(by_id["sleeve_alpha"].lot_ids) == {"lot_a_arkk", "lot_a_vti"}


def test_unsleeved_lots_become_their_own_unit():
    # Holdings assigned to no sleeve must not vanish from a coordinated run.
    snapshot = _uma_snapshot()
    from dataclasses import replace
    stripped = replace(snapshot, sleeve_assignments=tuple(
        pair for pair in snapshot.sleeve_assignments if pair[0] != "lot_b_aapl"))
    units = {u.unit_id: u for u in derive_units(stripped)}
    assert "acct_uma:unsleeved" in units
    assert units["acct_uma:unsleeved"].lot_ids == ("lot_b_aapl",)


def test_lot_in_two_sleeves_is_a_blocking_finding():
    accounts = [Account("acct_uma", TENANT, "tu_patel", Registration.TAXABLE, "Schwab", "o")]
    sleeves = [Sleeve("s1", "acct_uma", "m1", "model", None),
               Sleeve("s2", "acct_uma", "m2", "model", None)]
    lots = [make_lot("lot_x", "acct_uma", "VTI", "10", date(2020, 1, 1),
                     Money.of("1000.00"), BasisSource.CUSTODIAN_COVERED, True)]
    snapshot = build_snapshot(
        TENANT, "household_patel", AS_OF, accounts, lots, [],
        [CoverageInterval("acct_uma", date(2019, 1, 1), AS_OF, "s")], ["fx"],
        sleeves=sleeves, sleeve_assignments=[("lot_x", "s1"), ("lot_x", "s2")])
    assert any(f.code == codes.DUPLICATE_LOT for f in snapshot.blocking_findings())


def test_derived_account_units_when_there_are_no_sleeves(simple_snapshot):
    units = derive_units(simple_snapshot)
    assert {u.unit_id for u in units} == {"acct_tax", "acct_roth"}
    assert all(u.kind is UnitKind.ACCOUNT for u in units)
