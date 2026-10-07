"""Scripted rule cases, and proof that every rule has at least one.

Hand-picked unit tests can leave a rule in a pack that nothing exercises, and
nothing says so. These cases are data, so the suite can assert coverage over
the packs themselves.
"""
import os

import pytest
import yaml

from taxagent.application.bootstrap import PROJECT_ROOT
from case_runner import check, load_cases

PACKS_DIR = os.path.join(PROJECT_ROOT, "rules", "tax", "parameter-packs")
CASES = load_cases()


def pack_rules():
    """Every rule name declared by every parameter pack."""
    names = {}
    for filename in sorted(os.listdir(PACKS_DIR)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        pack = yaml.safe_load(open(os.path.join(PACKS_DIR, filename), encoding="utf-8"))
        for rule in pack.get("rules", {}):
            names[rule] = pack["bundle_id"]
    return names


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case):
    failures = check(case)
    assert not failures, f"{case['id']} ({case['_file']}): " + "; ".join(failures)


def test_every_rule_in_every_pack_is_exercised():
    exercised = {rule for case in CASES for rule in case.get("rules", [])}
    declared = pack_rules()
    missing = sorted(set(declared) - exercised)
    assert not missing, (
        "these rules are in a pack but no scripted case exercises them: "
        + ", ".join(f"{r} ({declared[r]})" for r in missing))


def test_no_case_names_a_rule_that_does_not_exist():
    declared = set(pack_rules())
    for case in CASES:
        unknown = set(case.get("rules", [])) - declared
        assert not unknown, f"{case['id']} names rules no pack declares: {sorted(unknown)}"


def test_every_case_is_documented_and_asserts_something():
    for case in CASES:
        assert case.get("id"), f"a case in {case['_file']} has no id"
        assert case.get("rules"), f"{case['id']} does not say which rules it exercises"
        assert len(case.get("description", "")) > 30, f"{case['id']} lacks a description"
        assert case.get("expect") or case.get("expect_contains"), \
            f"{case['id']} asserts nothing"


def test_case_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate case ids"


def test_each_rule_has_more_than_a_single_happy_path():
    # One case per rule proves it runs; it does not probe a boundary. The rules
    # with real edges carry several.
    counts = {}
    for case in CASES:
        for rule in case.get("rules", []):
            counts[rule] = counts.get(rule, 0) + 1
    thin = sorted(r for r in ("ordinary_brackets", "capital_gain_brackets", "niit",
                              "capital_loss_limit", "amt", "qbi",
                              "wash_sale_window_days")
                  if counts.get(r, 0) < 3)
    assert not thin, f"these rules have fewer than three cases: {thin}"


def test_cases_cover_every_filing_status():
    seen = set()
    for case in CASES:
        given = case.get("given") or {}
        if "filing_status" in given:
            seen.add(given["filing_status"])
    assert {"SINGLE", "MFJ", "MFS", "HOH"} <= seen, f"statuses never exercised: {seen}"
