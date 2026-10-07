"""The blueprint's worked example, end to end."""
from datetime import date

import pytest


@pytest.fixture
def run(service, principal, source_data, prices):
    def _run(intent, key, subject="acct_schwab_joint"):
        return service.start_analysis(principal, "req_1042", "harvest-review.v1", subject,
                                      source_data, date(2026, 9, 15), prices, intent, key)
    return _run


def test_ambiguous_gain_budget_asks_instead_of_guessing(run):
    intent = {
        "intent": "harvest_review", "mode": "analysis_only",
        "objectives": [{"type": "raise_cash", "amount": "50000.00", "currency": "USD",
                        "withdrawal_account": "acct_schwab_joint"}],
        "constraints": [{"type": "realized_gain_budget", "amount": "15000.00",
                         "currency": "USD", "netting_basis": None, "period": None}],
    }
    envelope = run(intent, "idem_ambiguous")
    assert envelope["status"] == "needs_input"
    assert {"netting_basis", "period"} <= set(envelope["unresolved_fields"])


def test_unreachable_cash_target_is_infeasible_not_relaxed(run):
    intent = {
        "intent": "harvest_review", "mode": "analysis_only",
        "objectives": [{"type": "raise_cash", "amount": "50000.00", "currency": "USD",
                        "withdrawal_account": "acct_schwab_joint"}],
        "constraints": [{"type": "realized_gain_budget", "amount": "15000.00",
                         "currency": "USD", "netting_basis": "net_gains", "period": "2026"}],
    }
    envelope = run(intent, "idem_infeasible")
    assert envelope["status"] == "blocked"
    assert envelope["code"] == "INFEASIBLE_CONSTRAINTS"
    assert envelope["data"]["diagnostics"]["cash_shortfall"] == "11000.00"
    # Provenance survives invalidation.
    assert envelope["snapshot_ref"]
    assert envelope["evidence_ref"]
    # The washed ARKK lot was excluded rather than harvested.
    flagged = envelope["data"]["diagnostics"]["wash_sale_flagged"]
    assert flagged[0]["lot_id"] == "lot_arkk_a"


def test_achievable_request_completes_with_limitations(run):
    intent = {
        "intent": "harvest_review", "mode": "analysis_only",
        "objectives": [{"type": "raise_cash", "amount": "10000.00", "currency": "USD",
                        "withdrawal_account": "acct_schwab_joint"}],
        "constraints": [{"type": "realized_gain_budget", "amount": "15000.00",
                         "currency": "USD", "netting_basis": "net_gains", "period": "2026"}],
    }
    envelope = run(intent, "idem_ok")
    assert envelope["status"] == "completed_with_limitations"
    # A completed calculation still reports incomplete real-world coverage.
    assert envelope["validation"]["external_account_coverage"] == "incomplete"
    codes = {limitation["code"] for limitation in envelope["limitations"]}
    assert "MISSING_BASIS" in codes
    assert "UNKNOWN_RELATED_ACCOUNTS" in codes
    assert envelope["execution_authorized"] is False


def test_repeated_request_is_idempotent(run, service, principal):
    intent = {
        "intent": "harvest_review", "mode": "analysis_only",
        "objectives": [{"type": "raise_cash", "amount": "10000.00", "currency": "USD",
                        "withdrawal_account": "acct_schwab_joint"}],
        "constraints": [{"type": "realized_gain_budget", "amount": "15000.00",
                         "currency": "USD", "netting_basis": "net_gains", "period": "2026"}],
    }
    first = run(intent, "idem_same")
    second = run(intent, "idem_same")
    assert second["status"] == "accepted"
    assert second["job_ref"] == first["data"]["run_id"]


def test_run_status_exposes_the_audit_trail(run, service, principal):
    intent = {
        "intent": "harvest_review", "mode": "analysis_only",
        "objectives": [{"type": "raise_cash", "amount": "10000.00", "currency": "USD",
                        "withdrawal_account": "acct_schwab_joint"}],
        "constraints": [{"type": "realized_gain_budget", "amount": "15000.00",
                         "currency": "USD", "netting_basis": "net_gains", "period": "2026"}],
    }
    envelope = run(intent, "idem_audit")
    status = service.get_run_status(principal, "req_s", envelope["data"]["run_id"])
    steps = [step["step_id"] for step in status["data"]["steps"]]
    assert steps == ["snapshot", "lot_check", "constraints", "candidates", "gain_loss",
                     "wash_sale", "validation", "evidence"]
    assert status["data"]["state"] == "COMPLETED"


# --- coordinated household and UMA runs ---------------------------------

def _coordinated_intent(cash):
    return {
        "intent": "household_coordination", "mode": "analysis_only",
        "objectives": [{"type": "raise_cash", "amount": cash, "currency": "USD",
                        "withdrawal_account": "acct_schwab_joint"}],
        "constraints": [{"type": "realized_gain_budget", "amount": "15000.00",
                         "currency": "USD", "netting_basis": "net_gains", "period": "2026"}],
    }


def test_household_runs_under_the_coordination_template(service, principal, source_data,
                                                        prices):
    # The same subject that harvest-review.v1 refuses is accepted here, because
    # a coordinator exists for household_coordinated scope.
    envelope = service.start_analysis(
        principal, "req_c1", "household-coordination.v1", "household_patel", source_data,
        date(2026, 9, 15), prices, _coordinated_intent("10000.00"), "idem_coord_1")
    assert envelope["status"] in {"completed", "completed_with_limitations"}
    package = service.store.get_artifact(principal.tenant_id, envelope["evidence_ref"])
    diagnostics = package["results"]["candidate"]["diagnostics"]
    assert diagnostics["coordination"] == "sequential"
    assert diagnostics["optimality_claimed"] is False
    assert diagnostics["order_dependent"] is True


def test_joint_household_scope_remains_unsupported(service, principal, source_data, prices,
                                                   harvest_intent):
    # Coordination does not unlock the `household` scope; a joint optimizer is
    # still required for that claim.
    envelope = service.start_analysis(
        principal, "req_c2", "harvest-review.v1", "household_patel", source_data,
        date(2026, 9, 15), prices, harvest_intent, "idem_coord_2")
    assert envelope["status"] == "blocked"
    assert envelope["code"] == "UNSUPPORTED_SCOPE"


def test_uma_sleeve_conflict_is_prevented_end_to_end(service, principal, prices, tenant):
    from decimal import Decimal

    from taxagent.connectors.uploaded_files import UploadedFileSource
    from taxagent.domain.sleeves import PlannedAcquisition

    uma = UploadedFileSource().load_file(tenant, "tests/fixtures/patel_uma.json")
    planned = [PlannedAcquisition("acct_uma", "ARKK", Decimal("300"),
                                  date(2026, 9, 25), "sleeve_beta")]
    envelope = service.start_analysis(
        principal, "req_uma", "household-coordination.v1", "acct_uma", uma,
        date(2026, 9, 15), prices, _coordinated_intent("10000.00"), "idem_uma_1",
        planned_acquisitions=planned)

    package = service.store.get_artifact(tenant, envelope["evidence_ref"])
    diagnostics = package["results"]["candidate"]["diagnostics"]
    conflicts = diagnostics["cross_unit_conflicts_prevented"]
    assert conflicts and conflicts[0]["selling_unit"] == "sleeve_alpha"
    # Alpha's ARKK loss was not harvested into Beta's intended purchase.
    assert "lot_a_arkk" not in {t["lot_id"] for t in package["results"]["candidate"]["trades"]}
    # Per-sleeve accounting is visible to the reviewer.
    assert {u["unit_id"] for u in diagnostics["per_unit"]} >= {"sleeve_alpha", "sleeve_beta"}
