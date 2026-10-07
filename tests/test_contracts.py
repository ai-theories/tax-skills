"""Schemas load, real payloads validate, and bad payloads are rejected."""
import json
import os

import pytest
from jsonschema import Draft202012Validator

from taxagent.application.bootstrap import CONTRACTS_DIR
from taxagent.gateway.validation import ContractError, ContractValidator


@pytest.fixture(scope="module")
def validator():
    return ContractValidator(CONTRACTS_DIR)


def test_every_schema_is_itself_valid(validator):
    for name in validator.names():
        Draft202012Validator.check_schema(validator.schema(name))


def test_intent_example_validates(validator, harvest_intent):
    validator.validate("intent", harvest_intent)


def test_unsupported_major_version_is_refused(validator, harvest_intent):
    payload = dict(harvest_intent, schema_version="2.0")
    with pytest.raises(ContractError):
        validator.validate("intent", payload)


def test_execution_mode_is_not_expressible(validator, harvest_intent):
    # There is no way to ask this release for an executing run.
    with pytest.raises(ContractError):
        validator.validate("intent", dict(harvest_intent, mode="execute"))


def test_unknown_field_is_rejected_not_ignored(validator, harvest_intent):
    with pytest.raises(ContractError):
        validator.validate("intent", dict(harvest_intent, tenant_id="tenant_ria_2"))


def test_money_must_be_a_decimal_string(validator, harvest_intent):
    payload = json.loads(json.dumps(harvest_intent))
    payload["objectives"][0]["amount"] = 10000.50        # float
    with pytest.raises(ContractError):
        validator.validate("intent", payload)


def test_tool_result_cannot_authorize_execution(validator):
    envelope = {"schema_version": "1.0", "request_id": "r", "status": "completed",
                "execution_authorized": True}
    with pytest.raises(ContractError):
        validator.validate("tool-result", envelope)


def test_evidence_package_validates(service, principal, source_data, prices, harvest_intent,
                                    validator):
    from datetime import date
    envelope = service.start_analysis(principal, "req_c", "harvest-review.v1",
                                      "acct_schwab_joint", source_data, date(2026, 9, 15),
                                      prices, harvest_intent, "idem_c")
    package = service.store.get_artifact(principal.tenant_id, envelope["evidence_ref"])
    validator.validate("evidence-package", package)


def test_agent_task_limits_are_bounded(validator):
    task = {"schema_version": "1.0", "task_id": "t1", "parent_run_id": "r1",
            "role": "source-extract", "objective": "Extract lots",
            "expected_output": "extracted-facts.v1",
            "limits": {"max_tool_calls": 999, "max_output_tokens": 3000}}
    with pytest.raises(ContractError):
        validator.validate("agent-task", task)


# --- schemas with no payload behind them ----------------------------------
# A schema that is valid JSON Schema but that nothing is ever validated
# against is a contract nobody honours: it can drift from what the code
# actually produces and no test notices. Eight of the fourteen were in that
# state. These validate real objects, built by the real code, against them.

def test_portfolio_snapshot_matches_its_schema(validator, service, principal,
                                               source_data, prices, harvest_intent):
    from datetime import date
    envelope = service.start_analysis(
        principal, "req_snap", "harvest-review.v1", "acct_schwab_joint", source_data,
        date(2026, 9, 15), prices, harvest_intent, "idem_snapshot_contract")
    snapshot = service.snapshots[envelope["snapshot_ref"]]
    validator.validate("portfolio-snapshot", snapshot.to_json())


def test_constraint_set_matches_its_schema(validator, service, principal,
                                           source_data, prices, harvest_intent):
    from datetime import date
    from taxagent.decisions.constraint_compiler import compile_constraints

    compiled = compile_constraints(
        tenant_id="tenant_ria_1", subject_id="acct_schwab_joint",
        intent=harvest_intent, restrictions=(), rule_bundle_ref="rules.v1")
    validator.validate("constraint-set", compiled.to_json())


def test_validation_result_matches_its_schema(validator, service, principal,
                                              source_data, prices, harvest_intent):
    from datetime import date
    envelope = service.start_analysis(
        principal, "req_val", "harvest-review.v1", "acct_schwab_joint", source_data,
        date(2026, 9, 15), prices, harvest_intent, "idem_validation_contract")
    package = service.store.get_artifact("tenant_ria_1", envelope["evidence_ref"])
    validator.validate("validation-result", package["results"]["validation"])


def test_every_registered_engine_matches_the_capability_schema(validator, service):
    """The registry file and the published contract must agree.

    Three engines were added to engines.yaml during this project. Nothing
    checked their shape against the schema that describes an engine, so a
    missing or misspelled field would have shipped.
    """
    for engine in service.capabilities.engines:
        validator.validate("engine-capability", engine.to_json())


def test_no_schema_is_left_without_a_payload(validator):
    """The guard that makes the four tests above impossible to lose again.

    Any schema not listed here must have a test validating a real payload
    against it. Adding a schema and no test fails this; so does deleting one
    of those tests.
    """
    exercised = {
        "intent", "tool-result", "evidence-package", "agent-task", "common",
        "portfolio-snapshot", "constraint-set", "validation-result",
        "engine-capability",
    }
    # These describe messages this release does not emit. Each names why.
    not_emitted = {
        "analysis-plan": "no planner produces one; plans are workflow templates",
        "tool-request": "the MCP server validates arguments against each tool's "
                        "own inputSchema instead",
        "workflow-event": "events are written to the store, not published",
        "agent-result": "no subagent returns through this boundary yet",
        "scenario": "scenarios are read from the evidence package",
    }
    declared = set(validator.names())
    unaccounted = declared - exercised - set(not_emitted)
    assert not unaccounted, (
        f"schemas with no payload and no stated reason: {sorted(unaccounted)}")
    stale = set(not_emitted) & exercised
    assert not stale, f"listed as not emitted but also exercised: {sorted(stale)}"
