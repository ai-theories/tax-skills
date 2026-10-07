"""The console's inventory must account for every scripted case, exactly once.

The console listed 71 of 301 cases, which made the rest invisible. An
inventory that silently drops a case is worse than no inventory, so these
tests tie it to the suites' own files rather than to a written total.
"""
import glob
import os

import pytest

from taxagent.console import inventory as inv
from taxagent.console import use_cases as uc


@pytest.fixture(scope="module")
def listed():
    return inv.cases()


def test_every_case_in_every_suite_is_listed(listed):
    counted = {"console": len(uc.USE_CASES), "rule": 0, "conformance": 0}
    for path in glob.glob(os.path.join(inv.CASE_DIR, "*.yaml")):
        counted["rule"] += len(inv._load(path))
    for path in glob.glob(os.path.join(inv.CASE_DIR, "optimizer", "*.yaml")):
        if not os.path.basename(path).startswith("_"):
            counted["conformance"] += len(inv._load(path))
    for suite, expected in counted.items():
        assert sum(1 for c in listed if c["suite"] == suite) == expected, suite


def test_no_case_is_listed_twice(listed):
    seen = [(c["suite"], c["id"]) for c in listed]
    assert len(seen) == len(set(seen))


def test_every_case_lands_in_a_declared_group(listed):
    assert {c["group"] for c in listed} <= set(inv.GROUP_ORDER)


def test_every_group_has_a_lead_and_members():
    report = inv.inventory()
    assert report["total"] == sum(g["count"] for g in report["groups"])
    for group in report["groups"]:
        assert group["count"], group["group"]
        assert len(group["lead"]) > 40, group["group"]


def test_only_console_cases_claim_to_be_runnable(listed):
    for case in listed:
        assert case["runnable"] == (case["suite"] == "console"), case["id"]
