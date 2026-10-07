"""Every scripted case in the repository, grouped by what the client wants.

The console used to list only the 71 cases it can run live, which left the
other 230 invisible — and coverage nobody can see is indistinguishable from
coverage that does not exist. This reads all three suites from their own
files, so the inventory cannot drift from what actually runs.

Grouping is by objective, the same grouping the written inventory uses, so the
two can be read against each other. `test_inventory.py` fails if a case in any
suite lands in no group or in two.
"""
from __future__ import annotations

import glob
import os
from typing import Any, Dict, List

import yaml

from ..application.bootstrap import PROJECT_ROOT
from . import use_cases as uc

CASE_DIR = os.path.join(PROJECT_ROOT, "tests", "cases")

HARVEST = "Harvest losses"
CASH = "Raise cash"
GAINS = "Cap realized gains"
REBALANCE = "Rebalance"
HOUSEHOLD = "Coordinate a household"
MUNI = "Municipal bonds"
FEDERAL = "Know what a gain costs"
BUSINESS = "Business owner and entity"
ESTATE = "Transfer wealth"
FILING = "Fix a filing problem"
EXPAT = "Expat and cross-border"
DEDUCTIBLE = "What is deductible"
DATA = "Trust the data"
BOUNDARY = "Entitlement, scope and replay"
INTEGRITY = "Conservation, restrictions and determinism"

GROUP_ORDER = [HARVEST, CASH, GAINS, REBALANCE, HOUSEHOLD, MUNI, FEDERAL, BUSINESS,
               ESTATE, FILING, EXPAT, DEDUCTIBLE, DATA, BOUNDARY, INTEGRITY]

LEAD = {
    HARVEST: "Tax-managed equity above all, then any taxable account in a down "
             "market. Very common, and the most mishandled: the rule reaches across "
             "accounts, across spouses, and into retirement plans where the loss is "
             "destroyed rather than deferred.",
    CASH: "Every segment. The objective is proceeds by a date, with the gain budget "
          "as the constraint rather than the goal.",
    GAINS: "UHNW and HNW accounts under a signed gain budget. Twelve of these are "
           "the optimality gate: the difference between a plan that works and a plan "
           "that is provably best.",
    REBALANCE: "Tax-managed equity and any model-driven book. Exact per overweight "
               "security, and it never sells a security below its target.",
    HOUSEHOLD: "UHNW families, UMAs with several managers, spouses at different "
               "custodians. Budgets belong to taxpayers and are never pooled.",
    MUNI: "UHNW and HNW in high-tax states, and anyone comparing a muni to a "
          "Treasury. The standard formula uses the federal rate alone and "
          "understates the equivalent yield by over a point.",
    FEDERAL: "Every segment, and where a wrong answer is most likely to be believed, "
             "because the output looks like a return even though it is not one.",
    BUSINESS: "Founders, practice owners, and anyone with pass-through income — at "
              "UHNW that is most clients.",
    ESTATE: "UHNW almost exclusively, plus any trustee filing a 1041. Lifetime gifts "
            "are added back to the estate, not subtracted from it.",
    FILING: "Every segment, and the group most often needed by a prospect rather "
            "than a client: someone arrives already late.",
    EXPAT: "Clients working abroad, and anyone receiving money from family overseas.",
    DEDUCTIBLE: "HNW and UHNW itemizers in high-tax states. The phase-down is a "
                "slope, not a cliff.",
    DATA: "Every segment, worst at UHNW. Unknown basis blocks rather than defaulting "
          "to zero, which is a question instead of a confident wrong answer.",
    BOUNDARY: "Every deployment. An account the caller may not read is refused in "
              "the same words as one that does not exist.",
    INTEGRITY: "Not client scenarios: the invariant battery every proposal is "
               "recomputed against, whatever engine produced it.",
}

# --- which group each case belongs to -------------------------------------

CONFORMANCE_GROUP = {
    "wash_sale": HARVEST, "selection": HARVEST,
    "cash_target": CASH, "infeasibility": CASH,
    "gain_budget": GAINS, "optimality": GAINS,
    "rebalance": REBALANCE,
    "scope": HOUSEHOLD,
    "data_quality": DATA,
    "conservation": INTEGRITY, "restrictions": INTEGRITY, "determinism": INTEGRITY,
}

RULE_GROUP = {
    "investment_rules": HARVEST,
    "municipal": MUNI,
    "federal_income": FEDERAL, "amt": FEDERAL,
    "qbi": BUSINESS,
    "estate_gift": ESTATE,
    "international": EXPAT,
}

# business_compliance_salt.yaml holds three domains; the id prefix says which.
PREFIX_GROUP = [("section-179", BUSINESS), ("entity-", BUSINESS),
                ("penalty-", FILING), ("safe-harbour-", FILING),
                ("salt-", DEDUCTIBLE)]

# Cases whose file would put them in the wrong group.
RULE_OVERRIDE = {"missing-basis-blocks-rather-than-defaulting-to-zero": DATA}

CONSOLE_GROUP = {
    "Household & UMA": HOUSEHOLD, "Estate & gift": ESTATE, "International": EXPAT,
    "Business entity": BUSINESS, "Compliance": FILING, "State & local": DEDUCTIBLE,
    "Municipal bonds": MUNI, "Federal": FEDERAL,
}

CONSOLE_OVERRIDE = {
    "federal-qbi-phase-in": BUSINESS, "federal-qbi-sstb": BUSINESS,
    "portfolio-lots": HARVEST, "portfolio-harvest": HARVEST,
    "portfolio-planned-purchase": HARVEST,
    "portfolio-infeasible": CASH,
    "portfolio-missing-netting": GAINS,
    "portfolio-rebalance": REBALANCE, "portfolio-rebalance-needs-targets": REBALANCE,
    "portfolio-intake": DATA,
    "portfolio-discover": BOUNDARY, "portfolio-denied": BOUNDARY,
    "portfolio-wrong-year": BOUNDARY, "portfolio-household-refused": BOUNDARY,
    "portfolio-no-execution": BOUNDARY, "portfolio-idempotent": BOUNDARY,
}


def _load(path: str) -> List[Dict[str, Any]]:
    loaded = yaml.safe_load(open(path, encoding="utf-8"))
    return loaded if isinstance(loaded, list) else loaded.get("cases", [])


def _rule_group(stem: str, case_id: str) -> str:
    if case_id in RULE_OVERRIDE:
        return RULE_OVERRIDE[case_id]
    if stem in RULE_GROUP:
        return RULE_GROUP[stem]
    for prefix, group in PREFIX_GROUP:
        if case_id.startswith(prefix):
            return group
    raise KeyError(f"{stem}/{case_id} belongs to no group")


def cases() -> List[Dict[str, Any]]:
    """Every scripted case, each tagged with its group, suite and source file."""
    found: List[Dict[str, Any]] = []

    for case in uc.USE_CASES:
        group = CONSOLE_OVERRIDE.get(case["id"]) or CONSOLE_GROUP[case["group"]]
        found.append({"id": case["id"], "group": group, "suite": "console",
                      "source": "src/taxagent/console/use_cases.py",
                      "pins": case["shows"], "runnable": True,
                      "detail": f"{case['server']} · {case['tool']} · "
                                f"expects {case['expect']}"})

    for path in sorted(glob.glob(os.path.join(CASE_DIR, "*.yaml"))):
        stem = os.path.basename(path)[:-5]
        for case in _load(path):
            found.append({
                "id": case["id"], "group": _rule_group(stem, case["id"]),
                "suite": "rule", "source": f"tests/cases/{stem}.yaml",
                "pins": case.get("description", ""), "runnable": False,
                "detail": "calc " + str(case.get("calc", ""))
                          + " · rules " + ", ".join(case.get("rules", []))})

    for path in sorted(glob.glob(os.path.join(CASE_DIR, "optimizer", "*.yaml"))):
        stem = os.path.basename(path)[:-5]
        if stem.startswith("_"):
            continue
        for case in _load(path):
            found.append({
                "id": case["id"], "group": CONFORMANCE_GROUP[stem],
                "suite": "conformance", "source": f"tests/cases/optimizer/{stem}.yaml",
                "pins": case.get("description", ""), "runnable": False,
                "detail": "category " + str(case.get("category", ""))
                          + " · portfolio " + str(case.get("portfolio_ref", ""))})

    return found


def inventory() -> Dict[str, Any]:
    """The whole inventory, grouped, for the console to render."""
    all_cases = cases()
    groups = []
    for group in GROUP_ORDER:
        members = [c for c in all_cases if c["group"] == group]
        groups.append({"group": group, "lead": LEAD[group], "count": len(members),
                       "cases": members})
    return {
        "total": len(all_cases),
        "runnable": sum(1 for c in all_cases if c["runnable"]),
        "suites": {suite: sum(1 for c in all_cases if c["suite"] == suite)
                   for suite in ("console", "rule", "conformance")},
        "groups": groups,
    }
