"""Coverage gating: an unsupported request is refused, never approximated."""
import pytest

from taxagent.domain import codes
from taxagent.gateway.capability_registry import CapabilityRegistry
from taxagent.optimization.interface import UnsupportedCapability
from taxagent.optimization.oracle_account_adapter import OracleAccountAdapter


def test_household_scope_is_refused(service, principal, source_data, prices, harvest_intent):
    from datetime import date
    envelope = service.start_analysis(
        principal, "req_1", "harvest-review.v1", "household_patel", source_data,
        date(2026, 9, 15), prices, harvest_intent, "idem_household_1")
    assert envelope["status"] == "blocked"
    assert envelope["code"] == codes.UNSUPPORTED_SCOPE
    # The refusal names what is available instead of silently degrading.
    assert "account" in (envelope["supported_alternative"] or "")


def test_unsupported_asset_class_is_refused(service):
    with pytest.raises(UnsupportedCapability) as excinfo:
        service.capabilities.require("selector", "account", ["MUNICIPAL_BOND"], 2026)
    assert excinfo.value.code == codes.UNSUPPORTED_ASSET


def test_unsupported_tax_year_is_refused(service):
    with pytest.raises(UnsupportedCapability) as excinfo:
        service.capabilities.require("selector", "account", ["US_EQUITY"], 2019)
    assert excinfo.value.code == codes.WRONG_RULE_YEAR


def test_no_tax_liability_engine_is_advertised(service):
    engine = service.capabilities.engine("tax-liability-engine")
    assert engine.status == "unavailable"
    with pytest.raises(UnsupportedCapability):
        service.capabilities.require("tax_calculator", "account", ["US_EQUITY"], 2026)


def test_every_advertised_capability_has_reference_tests(service):
    for engine in service.capabilities.engines:
        if engine.status == "implemented":
            assert engine.reference_tests, f"{engine.engine_id} claims support with no test"


def test_unconfigured_adapter_refuses_rather_than_guessing():
    adapter = OracleAccountAdapter(configured=False)
    with pytest.raises(UnsupportedCapability):
        adapter.solve(None if False else __import__("types").SimpleNamespace(scope="account"))


def test_no_execution_tool_is_registered(service, principal):
    payload = service.get_capabilities(principal, "req_1")["data"]
    names = {tool["name"] for tool in payload["tools"]}
    assert not names & {"execute_trade", "file_return", "place_order"}
    assert payload["execution_capabilities"] == []


def test_rebalance_now_runs_on_its_own_engine(service, principal, source_data,
                                              prices, harvest_intent):
    """Rebalancing was refused until an engine existed. One exists now.

    What must not change is where it runs: a rebalance priced by the harvest
    selector would optimise for losses and be recorded in evidence as a
    harvest. The template reaches the rebalancer or it refuses; it never
    silently reaches the selector.
    """
    from datetime import date
    envelope = service.start_analysis(
        principal, "req_rb", "account-rebalance.v1", "acct_schwab_joint", source_data,
        date(2026, 9, 15), prices, harvest_intent, "idem_rb")
    # Without target weights the engine refuses rather than inventing a target.
    assert envelope["status"] in {"blocked", "failed"}
    message = str(envelope.get("message", ""))
    assert "target weights" in message or "rebalanc" in message


def test_the_rebalancer_is_a_different_engine_from_the_selector(service):
    assert service.rebalancer.name == "rebalance-engine"
    assert service.selector.name == "rule-based-harvest-selector"
    assert service.rebalancer is not service.selector


def test_a_joint_household_optimizer_is_now_available(service):
    """It was unavailable for most of this project's life, deliberately.

    The engine is registered only because the conformance suite's optimality
    cases pass against it; `claims_optimality` is checked against that suite
    elsewhere.
    """
    engine = service.capabilities.engine("household-optimizer")
    assert engine.status == "implemented"
    assert engine.is_available
    assert engine.claims_optimality
    resolved = service.capabilities.require("optimizer", "household", ["US_EQUITY"], 2026)
    assert resolved.engine_id in {"household-optimizer", "oracle-account-adapter"}


def test_rebalance_skill_does_not_route_to_the_harvest_tool():
    """The refusal must stay reachable by its own name.

    Routing a rebalance at the harvest tool would price it against a
    loss-harvest objective and record the run in evidence as a harvest, so the
    skill names the rebalance tool and receives an honest refusal instead.
    """
    import os
    from taxagent.application.bootstrap import PROJECT_ROOT
    path = os.path.join(PROJECT_ROOT, "skills", "rebalance-review", "SKILL.md")
    text = open(path, encoding="utf-8").read()
    assert "`review_rebalance`" in text
    # Naming the harvest tool is fine, and necessary — the skill has to say not
    # to use it. What matters is that the prohibition is present, not that the
    # name is absent, which is what this asserted before the engine existed.
    if "`review_harvest`" in text:
        assert "Never substitute" in text or "does not" in text, (
            "the skill mentions the harvest tool without forbidding its use here")


def test_every_reference_test_actually_exists(service):
    """A capability is advertised on the strength of named tests.

    Nothing checked that those names resolve, so a registry entry could cite a
    test that was renamed, moved, or never written and still read as proof.
    Collect each one and fail on the first that pytest cannot find.
    """
    import subprocess
    import os
    from taxagent.application.bootstrap import PROJECT_ROOT

    cited = [(engine.engine_id, ref)
             for engine in service.capabilities.engines
             for ref in engine.reference_tests]
    assert cited, "no engine cites a reference test"

    missing = []
    for engine_id, ref in cited:
        path, _, name = ref.partition("::")
        full = os.path.join(PROJECT_ROOT, path)
        if not os.path.exists(full):
            missing.append(f"{engine_id}: {ref} (no such file)")
            continue
        with open(full, encoding="utf-8") as handle:
            if f"def {name}(" not in handle.read():
                missing.append(f"{engine_id}: {ref} (no such test)")
    assert not missing, "reference tests that do not exist: " + "; ".join(missing)


def test_the_skill_registry_matches_what_is_on_disk(service):
    """A skill present but undeclared is a capability the host cannot see.

    `get_capabilities` reads this registry, so three skills shipped on disk
    without an entry meant a host asking what the plugin can do got an
    incomplete answer. Both directions are checked: a declared skill with no
    directory is the same failure pointing the other way.
    """
    import os
    from taxagent.application.bootstrap import PROJECT_ROOT

    declared = {s["skill_id"] for s in service.capabilities.skills}
    skills_dir = os.path.join(PROJECT_ROOT, "skills")
    present = {name for name in os.listdir(skills_dir)
               if os.path.isdir(os.path.join(skills_dir, name))}
    assert declared == present, (
        f"declared but absent: {sorted(declared - present)}; "
        f"present but undeclared: {sorted(present - declared)}")


def test_every_declared_skill_has_a_skill_file(service):
    import os
    from taxagent.application.bootstrap import PROJECT_ROOT

    for skill in service.capabilities.skills:
        path = os.path.join(PROJECT_ROOT, "skills", skill["skill_id"], "SKILL.md")
        assert os.path.exists(path), f"{skill['skill_id']} has no SKILL.md"


def test_every_workflow_template_a_skill_names_exists(service):
    """A skill routing to a template that does not exist fails at call time."""
    known = set(service.templates.ids()) if hasattr(service.templates, "ids") else None
    if known is None:
        import os
        from taxagent.application.bootstrap import PROJECT_ROOT
        known = {f[:-5] for f in os.listdir(os.path.join(PROJECT_ROOT, "workflows"))
                 if f.endswith(".yaml")}
    for skill in service.capabilities.skills:
        for template in skill.get("workflow_templates") or []:
            assert template in known, f"{skill['skill_id']} names unknown {template}"
