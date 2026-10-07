"""Access is decided at the service boundary, never by the caller's claims."""
import pytest

from taxagent.domain.identities import Principal
from taxagent.gateway.authorization import AccessDenied


def test_other_tenants_subject_is_denied(service, principal):
    envelope = service.resolve_subject(principal, "req_1", "household_other")
    assert envelope["status"] == "blocked"
    assert envelope["code"] == "ACCESS_DENIED"


def test_missing_and_forbidden_subjects_are_indistinguishable(service, principal):
    forbidden = service.resolve_subject(principal, "req_1", "household_other")
    absent = service.resolve_subject(principal, "req_2", "household_does_not_exist")
    # Same code and same message: a denial must not confirm that an object exists.
    assert forbidden["code"] == absent["code"]
    assert forbidden["message"] == absent["message"]


def test_model_supplied_tenant_id_is_ignored(service, accounts, subjects, tenant):
    # A principal whose entitlements do not include the account cannot reach it
    # by naming another tenant, however the request is phrased.
    impostor = Principal("adv_x", "tenant_ria_2", frozenset({"analysis"}),
                         frozenset({"acct_schwab_joint"}))
    envelope = service.resolve_subject(impostor, "req_1", "acct_schwab_joint")
    assert envelope["status"] == "blocked"


def test_entitlements_narrow_the_resolved_scope(service, tenant):
    # Entitled to only one of the household's two accounts: the resolved
    # subject contains one, not both.
    limited = Principal("adv_2", tenant, frozenset({"analysis"}),
                        frozenset({"acct_schwab_joint"}))
    envelope = service.resolve_subject(limited, "req_1", "household_patel")
    assert envelope["data"]["account_ids"] == ["acct_schwab_joint"]


def test_run_from_another_tenant_is_not_readable(service, principal, store, tenant):
    run, _ = store.create_or_get(tenant, "k", {})
    other = Principal("adv_y", "tenant_ria_2", frozenset({"analysis"}), frozenset())
    assert service.get_run_status(other, "req_1", run["run_id"])["status"] == "blocked"
    assert service.get_run_status(principal, "req_2", run["run_id"])["status"] == "completed"
