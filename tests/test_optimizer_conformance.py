"""Acceptance suite every optimizer adapter must pass.

Registering an engine in `capabilities/engines.yaml` means this suite goes
green for it. Cases requiring optimality are skipped for adapters that do not
claim it — a greedy selector is not wrong for missing an optimum it never
promised — and those skips are the gate a real optimizer has to clear.
"""
import pytest

from optimizer_conformance import ADAPTERS, load_cases, run, run_twice

CASES = load_cases()
SOLVING = [c for c in CASES if c.get("kind") != "determinism"]
DETERMINISM = [c for c in CASES if c.get("kind") == "determinism"]


def adapters_for(case):
    """Which adapters a case runs against.

    A case that requires optimality and names no adapter runs against every
    adapter that claims it, plus the default selector — which skips, and that
    skip is the measured distance between feasible and optimal.
    """
    if "adapters" in case:
        return case["adapters"]
    if "optimality" in case.get("requires", []):
        # Only adapters that serve this case's scope. A household engine handed
        # an account problem refuses, correctly, and that refusal is not a
        # finding about its optimality.
        scope = case.get("scope", "account")
        objective = case.get("objective", "harvest_losses")
        claimers = sorted(n for n, a in ADAPTERS.items()
                          if a.claims_optimality and scope in a.scopes
                          and a.objective == objective)
        return claimers + ["rule-based-harvest-selector"]
    return ["rule-based-harvest-selector"]


def _pairs(cases):
    return [(c, name) for c in cases for name in adapters_for(c)]


SOLVING_PAIRS = _pairs(SOLVING)


@pytest.mark.parametrize("case,adapter_name", SOLVING_PAIRS,
                         ids=[f"{c['id']}[{n}]" for c, n in SOLVING_PAIRS])
def test_case(case, adapter_name):
    adapter = ADAPTERS[adapter_name]
    required = set(case.get("requires", []))
    missing = required - adapter.capabilities()
    if missing:
        pytest.skip(f"{adapter_name} does not claim {', '.join(sorted(missing))}; "
                    "this case is part of the gate it has yet to clear")
    failures = run(case, adapter)
    assert not failures, f"{case['id']} ({case['_file']}): " + "; ".join(failures)


@pytest.mark.parametrize("case", DETERMINISM, ids=[c["id"] for c in DETERMINISM])
def test_same_inputs_give_the_same_trades(case):
    for adapter_name in adapters_for(case):
        first, second = run_twice(case, ADAPTERS[adapter_name])
        assert first == second, (
            f"{case['id']}: {adapter_name} produced different trades on two runs of the "
            f"same problem — replay cannot be claimed")


# --- suite integrity ------------------------------------------------------

def test_the_suite_is_large_enough_to_be_meaningful():
    assert len(CASES) >= 100, f"only {len(CASES)} cases"


def test_case_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_no_case_asserts_something_the_checker_ignores():
    """An expectation the runner does not implement is silently true.

    Two rebalance cases named `drift_decreases` and `never_sells_past_target`
    before either was implemented. They passed, and they proved nothing.
    """
    from optimizer_conformance import KNOWN_EXPECTATIONS
    for case in CASES:
        unknown = set((case.get("expect") or {})) - KNOWN_EXPECTATIONS
        assert not unknown, f"{case['id']} asserts unchecked keys: {sorted(unknown)}"


def test_every_case_is_documented_and_categorised():
    for case in CASES:
        assert case.get("category"), f"{case['id']} has no category"
        assert len(case.get("description", "")) > 30, f"{case['id']} lacks a description"
        assert "portfolio_ref" in case or "portfolio" in case, f"{case['id']} has no portfolio"


def test_every_category_is_represented():
    categories = {c["category"] for c in CASES}
    assert categories >= {
        "conservation", "gain_budget", "cash_target", "restrictions", "wash_sale",
        "selection", "infeasibility", "data_quality", "determinism", "scope",
        "optimality"}


def test_every_referenced_portfolio_exists():
    from optimizer_conformance import PORTFOLIOS
    known = set(PORTFOLIOS["portfolios"])
    for case in CASES:
        if "portfolio_ref" in case:
            assert case["portfolio_ref"] in known, \
                f"{case['id']} references unknown portfolio {case['portfolio_ref']}"


def test_every_adapter_named_by_a_case_is_registered():
    for case in CASES:
        for name in adapters_for(case):
            assert name in ADAPTERS, f"{case['id']} names unregistered adapter {name}"


def test_optimality_cases_actually_run_against_a_claiming_adapter():
    """The gate must be cleared, not merely present.

    While no adapter claimed optimality these cases all skipped, which is the
    right behaviour for a greedy selector but says nothing about any optimizer.
    Now that one claims it, every gated case has to run against it — a suite
    where the hardest cases quietly skip is worse than no suite.
    """
    gated = [c for c in CASES if "optimality" in c.get("requires", [])]
    assert len(gated) >= 10, "the optimality gate needs real substance"
    claimers = {n for n, a in ADAPTERS.items() if a.claims_optimality}
    assert claimers, "no adapter claims optimality, so the gate is untested"
    for case in gated:
        running = set(adapters_for(case)) & claimers
        assert running, f"{case['id']} runs against no optimality-claiming adapter"


def test_no_registered_engine_claims_optimality_without_passing_the_gate():
    import os
    import yaml
    from taxagent.application.bootstrap import PROJECT_ROOT
    engines = yaml.safe_load(open(os.path.join(PROJECT_ROOT, "capabilities",
                                               "engines.yaml"), encoding="utf-8"))["engines"]
    for engine in engines:
        if engine.get("claims_optimality"):
            adapter = ADAPTERS.get(engine["engine_id"])
            assert adapter is not None and adapter.claims_optimality, (
                f"{engine['engine_id']} claims optimality in the registry but is not "
                "wired into the conformance suite")
