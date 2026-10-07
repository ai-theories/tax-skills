"""Runner for scripted rule cases.

A case is declarative: synthetic inputs, the rules it exercises, and the exact
figures expected. Keeping them as data rather than code means the suite can be
checked for *coverage* — every rule in every parameter pack must be named by at
least one case, so a rule cannot sit in a pack unexercised.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Dict, List

import yaml

from taxagent.application.bootstrap import PROJECT_ROOT
from taxagent.domain.coverage import CoverageInterval
from taxagent.domain.identities import Account, Registration
from taxagent.domain.money import Money
from taxagent.domain.tax_lots import Action, BasisSource, Transaction, make_lot
from taxagent.portfolio.snapshot_builder import build_snapshot
from taxagent.tax import (business, compliance, estate, federal, international,
                          municipal, salt)
from taxagent.tax.lot_gain_loss import ProposedSale, calculate_gain_loss
from taxagent.tax.wash_sale_screen import screen_wash_sales

CASES_DIR = os.path.join(PROJECT_ROOT, "tests", "cases")
TENANT = "tenant_cases"


def load_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(CASES_DIR)):
        if not name.endswith((".yaml", ".yml")):
            continue
        with open(os.path.join(CASES_DIR, name), "r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or []
        for case in loaded:
            case["_file"] = name
            cases.append(case)
    return cases


# --- direct engine calls -------------------------------------------------

CALCULATIONS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "ordinary_tax": lambda g: federal.calculate_ordinary_tax(
        g["taxable_income"], g.get("filing_status", "MFJ")),
    "capital_gain": lambda g: federal.calculate_capital_gain_tax(
        g["gain"], g.get("ordinary_taxable_income", "0"), g.get("filing_status", "MFJ")),
    "niit": lambda g: federal.calculate_niit(
        g["net_investment_income"], g["magi"], g.get("filing_status", "MFJ")),
    "capital_loss": lambda g: federal.calculate_capital_loss_deduction(
        g["net_capital_loss"], g.get("filing_status", "MFJ")),
    "federal_summary": lambda g: federal.summarize_federal(
        g["ordinary_income"], g.get("filing_status", "MFJ"),
        long_term_gain=g.get("long_term_gain", "0"),
        deduction=g.get("deduction"),
        net_investment_income=g.get("net_investment_income", "0")),
    "amt": lambda g: federal.calculate_amt(
        g["amti"], g["regular_tax"], g.get("filing_status", "MFJ"),
        net_capital_gain=g.get("net_capital_gain", "0")),
    "qbi": lambda g: federal.calculate_qbi_deduction(
        g["qualified_business_income"], g["taxable_income"],
        g.get("filing_status", "MFJ"), w2_wages=g.get("w2_wages", "0"),
        ubia=g.get("ubia", "0"), net_capital_gain=g.get("net_capital_gain", "0"),
        is_sstb=bool(g.get("is_sstb", False))),

    # --- estate and gift ---------------------------------------------------
    "estate_tax": lambda g: estate.calculate_estate_tax(
        g["gross_estate"], deductions=g.get("deductions", "0"),
        lifetime_taxable_gifts=g.get("lifetime_taxable_gifts", "0"),
        dsue_amount=g.get("dsue_amount", "0")),
    "gift_exclusion": lambda g: estate.calculate_gift_exclusion(
        g["gift_amount"], recipients=int(g.get("recipients", 1)),
        donors=int(g.get("donors", 1)),
        noncitizen_spouse=bool(g.get("noncitizen_spouse", False))),
    "fiduciary_tax": lambda g: estate.calculate_fiduciary_income_tax(
        g["ordinary_income"], long_term_gain=g.get("long_term_gain", "0")),

    # --- international -----------------------------------------------------
    "feie": lambda g: international.calculate_foreign_earned_income_exclusion(
        g["foreign_earned_income"], qualifying_days=int(g.get("qualifying_days", 365)),
        housing_expenses=g.get("housing_expenses", "0")),
    "foreign_gift": lambda g: international.check_foreign_gift_reporting(
        g["amount_received"], source=g.get("source", "individual")),

    # --- business ----------------------------------------------------------
    "section_179": lambda g: business.calculate_section_179(
        g["cost_placed_in_service"], elected_amount=g.get("elected_amount"),
        business_taxable_income=g.get("business_taxable_income")),
    "entity_comparison": lambda g: business.compare_entity_tax(
        g["business_income"], g["owner_marginal_rate"],
        distribution_fraction=g.get("distribution_fraction", "1.00"),
        qualified_dividend_rate=g.get("qualified_dividend_rate", "0.20"),
        passthrough_deduction=g.get("passthrough_deduction", "0")),

    # --- compliance --------------------------------------------------------
    "filing_penalty": lambda g: compliance.calculate_filing_penalty(
        g["unpaid_tax"], days_late=int(g["days_late"]),
        filed=bool(g.get("filed", False))),
    "entity_penalty": lambda g: compliance.calculate_entity_filing_penalty(
        g["entity_type"], owners=int(g["owners"]),
        months_late=int(g["months_late"])),
    "safe_harbour": lambda g: compliance.calculate_estimated_tax_safe_harbour(
        g["prior_year_tax"], g["current_year_projected_tax"], g["prior_year_agi"],
        withholding_and_payments=g.get("withholding_and_payments", "0"),
        filing_status=g.get("filing_status", "MFJ")),

    # --- municipal bonds ---------------------------------------------------
    "muni_yield": lambda g: municipal.calculate_taxable_equivalent_yield(
        g["municipal_yield"], g["federal_rate"], state_rate=g.get("state_rate", "0"),
        niit_applies=bool(g.get("niit_applies", False)),
        state_exempt=bool(g.get("state_exempt", True)),
        private_activity=bool(g.get("private_activity", False)),
        amt_applies=bool(g.get("amt_applies", False)),
        issue_year=(int(g["issue_year"]) if g.get("issue_year") else None)),
    "muni_discount": lambda g: municipal.calculate_market_discount(
        g["purchase_price"], g["redemption_price"],
        years_to_maturity=int(g["years_to_maturity"])),
    "muni_premium": lambda g: municipal.calculate_bond_premium(
        g["purchase_price"], g["redemption_price"],
        years_to_maturity=int(g["years_to_maturity"])),

    # --- state and local ---------------------------------------------------
    "salt_cap": lambda g: salt.calculate_salt_cap(
        g["state_and_local_tax_paid"], g["magi"], g.get("filing_status", "MFJ")),
}


# --- synthetic portfolios ------------------------------------------------

def build_portfolio(spec: Dict[str, Any]):
    """Turn a case's `portfolio` block into a frozen snapshot."""
    as_of = date.fromisoformat(spec["as_of"])
    accounts = [
        Account(a["id"], TENANT, a.get("tax_unit", "tu_1"),
                Registration(a.get("registration", "TAXABLE")),
                a.get("custodian", "Synthetic"), a.get("owner", "owner_1"))
        for a in spec["accounts"]
    ]
    lots = [
        make_lot(l["id"], l["account"], l["security"], str(l["quantity"]),
                 date.fromisoformat(l["acquired"]) if l.get("acquired") else None,
                 Money.of(str(l["basis"])) if l.get("basis") is not None else None,
                 BasisSource(l.get("basis_source", "CUSTODIAN_COVERED")),
                 l.get("covered", True))
        for l in spec.get("lots", [])
    ]
    transactions = [
        Transaction(t["id"], t["account"], t["security"], Action(t["action"]),
                    date.fromisoformat(t["date"]), Decimal(str(t["quantity"])),
                    Money.of(str(t["amount"])) if t.get("amount") is not None else None)
        for t in spec.get("transactions", [])
    ]
    coverage = [
        CoverageInterval(c["account"],
                         date.fromisoformat(c["start"]) if c.get("start") else None,
                         date.fromisoformat(c["end"]) if c.get("end") else None,
                         c.get("source", "synthetic"))
        for c in spec.get("coverage",
                          [{"account": a["id"], "start": "2015-01-01",
                            "end": spec["as_of"]} for a in spec["accounts"]])
    ]
    return build_snapshot(TENANT, spec.get("subject", "subject_1"), as_of,
                          accounts, lots, transactions, coverage, ["synthetic"])


def run_portfolio_case(case: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = build_portfolio(case["portfolio"])
    sales = [
        ProposedSale(s["account"], s["security"], Decimal(str(s["quantity"])),
                     Money.of(str(s["price"])), lot_id=s.get("lot"),
                     sale_date=date.fromisoformat(s["date"]) if s.get("date") else None)
        for s in case.get("sell", [])
    ]
    gain_loss = calculate_gain_loss(snapshot, sales, snapshot.as_of)
    payload: Dict[str, Any] = {"gain_loss": gain_loss.to_json()}
    if case.get("screen_wash_sales", True):
        screen = screen_wash_sales(
            snapshot, gain_loss.dispositions,
            known_related_account_ids=case.get("known_related_accounts", ()))
        payload["wash_sale"] = screen.to_json()
    payload["snapshot"] = {"findings": [f.to_json() for f in snapshot.findings]}
    return payload


def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    if "portfolio" in case:
        return run_portfolio_case(case)
    calc = CALCULATIONS[case["calc"]]
    return calc(case["given"]).to_json()


# --- expectations --------------------------------------------------------

def resolve(payload: Any, path: str) -> Any:
    """Read a dotted path, with [n] indexing for lists."""
    node = payload
    for part in path.split("."):
        if part.endswith("]") and "[" in part:
            name, index = part[:-1].split("[")
            node = node[name] if name else node
            node = node[int(index)]
        else:
            node = node[part]
    return node


def check(case: Dict[str, Any]) -> List[str]:
    """Return a list of failures; empty means the case passed."""
    payload = run_case(case)
    failures: List[str] = []
    for path, expected in (case.get("expect") or {}).items():
        try:
            actual = resolve(payload, path)
        except (KeyError, IndexError, TypeError) as exc:
            failures.append(f"{path}: not present ({exc})")
            continue
        if isinstance(expected, bool) or expected is None:
            if actual is not expected:
                failures.append(f"{path}: expected {expected!r}, got {actual!r}")
        elif str(actual) != str(expected):
            failures.append(f"{path}: expected {expected!r}, got {actual!r}")
    for path in (case.get("expect_contains") or {}):
        actual = str(resolve(payload, path))
        needle = str(case["expect_contains"][path])
        if needle not in actual:
            failures.append(f"{path}: expected to contain {needle!r}, got {actual!r}")
    return failures
