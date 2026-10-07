"""Evidence packages must be reproducible and fully claim-mapped."""
from datetime import date

from taxagent.evidence.package_builder import CLAIM_MAP, resolve_claim
from taxagent.evidence.report_renderer import render_review_brief


def _run(service, principal, source_data, prices, intent, key="idem_e1"):
    return service.start_analysis(principal, "req_e", "harvest-review.v1", "acct_schwab_joint",
                                  source_data, date(2026, 9, 15), prices, intent, key)


def test_replay_produces_an_identical_hash(service, principal, source_data, prices,
                                           harvest_intent, subjects, accounts):
    from taxagent.application.bootstrap import build_service
    from taxagent.storage.sqlite import SqliteStore

    first = _run(service, principal, source_data, prices, harvest_intent, "idem_a")
    second_service = build_service(subjects, accounts, store=SqliteStore())
    second = _run(second_service, principal, source_data, prices, harvest_intent, "idem_b")

    first_pack = service.store.get_artifact(principal.tenant_id, first["evidence_ref"])
    second_pack = second_service.store.get_artifact(principal.tenant_id, second["evidence_ref"])
    # Same inputs, different process and store: the content hash must match, or
    # "reproducible" is not a claim this system can make.
    assert first_pack["content_hash"] == second_pack["content_hash"]


def test_every_claim_resolves_to_a_field(service, principal, source_data, prices, harvest_intent):
    envelope = _run(service, principal, source_data, prices, harvest_intent)
    package = service.store.get_artifact(principal.tenant_id, envelope["evidence_ref"])
    for claim in package["claim_map"]:
        resolve_claim(package, claim)   # raises if the pointer is dead
    assert set(CLAIM_MAP) <= set(package["claim_map"])


def test_coordinated_run_publishes_its_optimality_gap(service, principal, source_data, prices):
    intent = {
        "intent": "household_coordination", "mode": "analysis_only",
        "objectives": [{"type": "raise_cash", "amount": "10000.00", "currency": "USD",
                        "withdrawal_account": "acct_schwab_joint"}],
        "constraints": [{"type": "realized_gain_budget", "amount": "15000.00",
                         "currency": "USD", "netting_basis": "net_gains", "period": "2026"}],
    }
    envelope = service.start_analysis(
        principal, "req_gap", "household-coordination.v1", "household_patel", source_data,
        date(2026, 9, 15), prices, intent, "idem_gap")
    package = service.store.get_artifact(principal.tenant_id, envelope["evidence_ref"])
    # The coordination claims exist and resolve only on coordinated runs.
    assert "optimality_gap" in package["claim_map"]
    for claim in package["claim_map"]:
        resolve_claim(package, claim)
    assert resolve_claim(package, "provably_optimal") in {True, False}


def test_package_pins_versions_and_input_hashes(service, principal, source_data, prices,
                                                harvest_intent):
    envelope = _run(service, principal, source_data, prices, harvest_intent)
    package = service.store.get_artifact(principal.tenant_id, envelope["evidence_ref"])
    manifest = package["engine_manifest"]
    assert manifest["package_version"] and manifest["python_version"]
    assert manifest["calculation_versions"]["wash_sale_screen"]
    assert package["inputs"]["snapshot_hash"].startswith("sha256:")
    assert set(package["provenance"]) == {"snapshot", "constraints", "candidate"}


def test_brief_names_the_rule_bundle_it_applied(service, principal, source_data, prices,
                                                harvest_intent):
    envelope = _run(service, principal, source_data, prices, harvest_intent, "idem_brief_rules")
    package = service.store.get_artifact(principal.tenant_id, envelope["evidence_ref"])
    brief = render_review_brief(package)
    assert "us-federal-investment-rules.v1" in brief
    assert "UNPINNED" not in brief


def test_package_is_analysis_only(service, principal, source_data, prices, harvest_intent):
    envelope = _run(service, principal, source_data, prices, harvest_intent)
    package = service.store.get_artifact(principal.tenant_id, envelope["evidence_ref"])
    assert package["mode"] == "analysis_only"
    assert package["execution_authorized"] is False


def test_report_names_which_account_each_limitation_concerns(service, principal, source_data,
                                                             prices, harvest_intent):
    envelope = _run(service, principal, source_data, prices, harvest_intent, "idem_refs")
    package = service.store.get_artifact(principal.tenant_id, envelope["evidence_ref"])
    brief = render_review_brief(package)
    unknown = [l for l in brief.splitlines() if "UNKNOWN_RELATED_ACCOUNTS" in l]
    # Two unscreened accounts produce two distinguishable lines, not two identical ones.
    assert len(unknown) == len(set(unknown))
    assert any("acct_spouse_ext" in line for line in unknown)


def test_report_states_limitations_and_never_claims_optimality(service, principal, source_data,
                                                               prices, harvest_intent):
    envelope = _run(service, principal, source_data, prices, harvest_intent)
    package = service.store.get_artifact(principal.tenant_id, envelope["evidence_ref"])
    brief = render_review_brief(package)
    assert "Limitations" in brief
    assert "optimal" not in brief.lower()
    assert "not tax advice" in brief.lower()
