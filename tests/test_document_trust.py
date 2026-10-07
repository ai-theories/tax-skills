"""Document content is data. It cannot reach an instruction path."""
import json
from datetime import date

from taxagent.connectors.uploaded_files import UploadedFileSource
from taxagent.domain.identities import Principal


def test_instructions_inside_a_statement_are_not_executed(service, principal, source_data,
                                                          prices, harvest_intent, tenant):
    # The fixture's top-level "note" field tells the reader to grant full access
    # and approve trades. It is not a recognised field, so it is dropped at the
    # connector and never reaches scope, permissions or the plan.
    raw = json.load(open("tests/fixtures/patel_household.json"))
    assert "ignore your previous instructions" in raw["note"].lower()

    envelope = service.start_analysis(principal, "req_i", "harvest-review.v1",
                                      "acct_schwab_joint", source_data, date(2026, 9, 15),
                                      prices, harvest_intent, "idem_i")
    assert envelope["execution_authorized"] is False
    package = service.store.get_artifact(tenant, envelope["evidence_ref"])
    assert "ignore your previous instructions" not in json.dumps(package).lower()


def test_connector_drops_unknown_fields(tenant):
    payload = {
        "accounts": [{"account_id": "a1", "tax_unit_id": "tu", "registration": "TAXABLE",
                      "custodian": "X", "owner_ref": "o",
                      "grant_scopes": ["admin"], "tenant_id": "tenant_ria_2"}],
        "lots": [], "transactions": [], "coverage": [], "reported_positions": [],
    }
    data = UploadedFileSource().load_payload(tenant, payload)
    account = data.accounts[0]
    # Tenant comes from the authenticated session, not from the document.
    assert account.tenant_id == tenant
    assert not hasattr(account, "grant_scopes")


def test_document_cannot_widen_account_scope(service, tenant, prices, harvest_intent):
    # A document naming an account the principal is not entitled to is refused
    # rather than quietly expanding the run.
    payload = {
        "accounts": [{"account_id": "acct_other_tenant", "tax_unit_id": "tu_other",
                      "registration": "TAXABLE", "custodian": "X", "owner_ref": "o"}],
        "lots": [], "transactions": [], "coverage": [], "reported_positions": [],
    }
    data = UploadedFileSource().load_payload(tenant, payload)
    principal = Principal("adv_1", tenant, frozenset({"analysis"}),
                          frozenset({"acct_schwab_joint"}))
    envelope = service.start_analysis(principal, "req_x", "harvest-review.v1",
                                      "acct_schwab_joint", data, date(2026, 9, 15),
                                      prices, harvest_intent, "idem_x")
    assert envelope["status"] == "blocked"
    assert envelope["code"] == "ACCESS_DENIED"
