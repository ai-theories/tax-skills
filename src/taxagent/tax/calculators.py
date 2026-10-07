"""The federal calculator surface, defined once.

The dashboard, the CLI and the federal MCP server all offer the same
calculations. Defined separately they would drift: a field renamed in the page
and not in the tool, a default changed in one place, a calculator added to one
surface and forgotten in the other. The specs below are the single definition,
and each surface derives its own shape from them.

Nothing here holds client data. Every figure is supplied by the caller, so
these calculations need no entitlement and no document.
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import (business, compliance, estate, federal, international,
               municipal, salt)

STATUSES = ["MFJ", "SINGLE", "HOH", "MFS"]
STATUS_FIELD = {"name": "status", "label": "Filing status", "type": "select",
                "options": STATUSES, "default": "MFJ"}

CALCULATORS: List[Dict[str, Any]] = [
    {"id": "federal", "domain": "federal", "title": "Federal estimate",
     "summary": "Ordinary tax, long-term gains stacked across the preferential bands, "
                "and the 3.8% surtax.",
     "fields": [
         {"name": "income", "label": "Ordinary income", "type": "money", "default": "185000"},
         STATUS_FIELD,
         {"name": "gain", "label": "Net long-term gain", "type": "money", "default": "50000"},
         {"name": "nii", "label": "Net investment income", "type": "money", "default": "50000"},
         {"name": "deduction", "label": "Itemized deduction (blank = standard)",
          "type": "money", "default": ""},
     ]},
    {"id": "capgains", "domain": "federal", "title": "Capital gain",
     "summary": "Where a gain falls across the 0/15/20 bands once it is stacked on "
                "ordinary income.",
     "fields": [
         {"name": "gain", "label": "Net long-term gain", "type": "money", "default": "100000"},
         {"name": "ordinary", "label": "Ordinary taxable income", "type": "money",
          "default": "90000"},
         STATUS_FIELD,
     ]},
    {"id": "niit", "domain": "federal", "title": "Net investment income tax",
     "summary": "3.8% on the lesser of net investment income or the MAGI excess.",
     "fields": [
         {"name": "nii", "label": "Net investment income", "type": "money",
          "default": "45000"},
         {"name": "magi", "label": "MAGI", "type": "money", "default": "260000"},
         STATUS_FIELD,
     ]},
    {"id": "amt", "domain": "federal", "title": "Alternative minimum tax",
     "summary": "Tentative minimum tax against regular tax, with the 50% exemption "
                "phase-out and preferential rates kept on gains.",
     "fields": [
         {"name": "amti", "label": "AMTI", "type": "money", "default": "600000"},
         {"name": "regular_tax", "label": "Regular tax", "type": "money",
          "default": "120000"},
         {"name": "gain", "label": "Net capital gain inside AMTI", "type": "money",
          "default": "0"},
         STATUS_FIELD,
     ]},
    {"id": "qbi", "domain": "federal", "title": "Section 199A deduction",
     "summary": "Qualified business income, with the wage and property limit phasing in "
                "and the service-business phase-out.",
     "fields": [
         {"name": "qbi", "label": "Qualified business income", "type": "money",
          "default": "200000"},
         {"name": "taxable_income", "label": "Taxable income before the deduction",
          "type": "money", "default": "600000"},
         {"name": "wages", "label": "W-2 wages", "type": "money", "default": "50000"},
         {"name": "ubia", "label": "Qualified property (UBIA)", "type": "money",
          "default": "0"},
         {"name": "sstb", "label": "Specified service business", "type": "select",
          "options": ["no", "yes"], "default": "no"},
         STATUS_FIELD,
     ]},
    {"id": "loss", "domain": "federal", "title": "Capital loss",
     "summary": "How much of a net capital loss is deductible now, and what carries forward.",
     "fields": [
         {"name": "loss", "label": "Net capital loss", "type": "money", "default": "12000"},
         STATUS_FIELD,
     ]},
]

# --- estate and gift -----------------------------------------------------
CALCULATORS += [
    {"id": "estate_tax", "domain": "estate", "title": "Federal estate tax",
     "summary": "Taxable estate and lifetime gifts against the unified credit, with the "
                "section 2001(c) schedule.",
     "fields": [
         {"name": "gross_estate", "label": "Gross estate", "type": "money",
          "default": "20000000"},
         {"name": "deductions", "label": "Deductions (marital, charitable, debts)",
          "type": "money", "default": "500000"},
         {"name": "lifetime_taxable_gifts", "label": "Lifetime taxable gifts",
          "type": "money", "default": "2000000"},
         {"name": "dsue_amount", "label": "Deceased spouse's unused exclusion",
          "type": "money", "default": "0"},
     ]},
    {"id": "gift_exclusion", "domain": "estate", "title": "Annual gift exclusion",
     "summary": "How much of a gift the annual exclusion covers, with splitting and the "
                "separate figure for a non-citizen spouse.",
     "fields": [
         {"name": "gift_amount", "label": "Gift amount", "type": "money",
          "default": "50000"},
         {"name": "recipients", "label": "Number of recipients", "type": "integer",
          "default": "1"},
         {"name": "donors", "label": "Donors (2 = gift splitting)", "type": "integer",
          "default": "1"},
         {"name": "noncitizen_spouse", "label": "Gift to a non-citizen spouse",
          "type": "select", "options": ["no", "yes"], "default": "no"},
     ]},
    {"id": "fiduciary_tax", "domain": "estate", "title": "Estate or trust income tax",
     "summary": "The compressed fiduciary brackets, which reach the top rate at $16,000.",
     "fields": [
         {"name": "ordinary_income", "label": "Ordinary taxable income", "type": "money",
          "default": "20000"},
         {"name": "long_term_gain", "label": "Net long-term gain", "type": "money",
          "default": "10000"},
     ]},
]

# --- international --------------------------------------------------------
CALCULATORS += [
    {"id": "feie", "domain": "international",
     "title": "Foreign earned income exclusion",
     "summary": "The exclusion and housing amount a qualifying taxpayer could claim. "
                "Does not establish that they qualify.",
     "fields": [
         {"name": "foreign_earned_income", "label": "Foreign earned income",
          "type": "money", "default": "180000"},
         {"name": "qualifying_days", "label": "Qualifying days in the period",
          "type": "integer", "default": "365"},
         {"name": "housing_expenses", "label": "Housing expenses", "type": "money",
          "default": "45000"},
     ]},
    {"id": "foreign_gift", "domain": "international",
     "title": "Gift from abroad: reporting",
     "summary": "Whether a gift from a foreign person crosses the Form 3520 threshold. "
                "Reporting, not tax.",
     "fields": [
         {"name": "amount_received", "label": "Amount received", "type": "money",
          "default": "150000"},
         {"name": "source", "label": "Donor", "type": "select",
          "options": ["individual", "estate", "corporation", "partnership"],
          "default": "individual"},
     ]},
]

# --- business entity ------------------------------------------------------
CALCULATORS += [
    {"id": "section_179", "domain": "business", "title": "Section 179 expensing",
     "summary": "The dollar limit falling one-for-one with spending, and the separate "
                "business income limit with its carryforward.",
     "fields": [
         {"name": "cost_placed_in_service", "label": "Cost placed in service",
          "type": "money", "default": "4500000"},
         {"name": "elected_amount", "label": "Amount elected (blank = all)",
          "type": "money", "default": ""},
         {"name": "business_taxable_income",
          "label": "Business taxable income (blank = skip that limit)",
          "type": "money", "default": ""},
     ]},
    {"id": "entity_comparison", "domain": "business",
     "title": "C corporation versus pass-through",
     "summary": "Federal tax on the same income both ways, over rates you supply. "
                "Arithmetic, not entity-choice advice.",
     "fields": [
         {"name": "business_income", "label": "Business income", "type": "money",
          "default": "1000000"},
         {"name": "owner_marginal_rate", "label": "Owner's marginal rate (e.g. 0.37)",
          "type": "money", "default": "0.37"},
         {"name": "distribution_fraction", "label": "Fraction of profit distributed",
          "type": "money", "default": "1.00"},
         {"name": "qualified_dividend_rate", "label": "Dividend rate", "type": "money",
          "default": "0.20"},
         {"name": "passthrough_deduction", "label": "Section 199A deduction",
          "type": "money", "default": "0"},
     ]},
]

# --- compliance and penalties ---------------------------------------------
CALCULATORS += [
    {"id": "filing_penalty", "domain": "compliance",
     "title": "Late filing and late payment",
     "summary": "Both additions to tax, with the section 6651(c) offset for months in "
                "which both run. Interest is not included.",
     "fields": [
         {"name": "unpaid_tax", "label": "Unpaid tax", "type": "money",
          "default": "10000"},
         {"name": "days_late", "label": "Days late", "type": "integer",
          "default": "95"},
         {"name": "filed", "label": "Return was filed on time", "type": "select",
          "options": ["no", "yes"], "default": "no"},
     ]},
    {"id": "entity_penalty", "domain": "compliance",
     "title": "Partnership or S corporation late filing",
     "summary": "Charged per owner per month regardless of tax owed, which is the part "
                "that surprises people.",
     "fields": [
         {"name": "entity_type", "label": "Entity", "type": "select",
          "options": ["partnership", "s_corporation"], "default": "partnership"},
         {"name": "owners", "label": "Partners or shareholders", "type": "integer",
          "default": "8"},
         {"name": "months_late", "label": "Months late", "type": "integer",
          "default": "4"},
     ]},
    {"id": "safe_harbour", "domain": "compliance",
     "title": "Estimated tax safe harbour",
     "summary": "The smaller of the current-year and prior-year routes, with the higher "
                "prior-year percentage for higher incomes.",
     "fields": [
         {"name": "prior_year_tax", "label": "Last year's tax", "type": "money",
          "default": "80000"},
         {"name": "current_year_projected_tax", "label": "This year's projected tax",
          "type": "money", "default": "200000"},
         {"name": "prior_year_agi", "label": "Last year's AGI", "type": "money",
          "default": "400000"},
         {"name": "withholding_and_payments", "label": "Withholding and payments so far",
          "type": "money", "default": "50000"},
         STATUS_FIELD,
     ]},
]

# --- state and local ------------------------------------------------------
CALCULATORS += [
    {"id": "salt_cap", "domain": "salt", "title": "State and local tax deduction cap",
     "summary": "The federal cap phasing down at 30 cents per dollar of MAGI above the "
                "threshold, to a floor. Not a cliff.",
     "fields": [
         {"name": "state_and_local_tax_paid", "label": "State and local tax paid",
          "type": "money", "default": "60000"},
         {"name": "magi", "label": "Modified AGI", "type": "money", "default": "550000"},
         STATUS_FIELD,
     ]},
    {"id": "state_coverage", "domain": "salt", "title": "State coverage",
     "summary": "Which states' own tax this deployment can compute, and what a state "
                "pack would have to contain.",
     "fields": [
         {"name": "state", "label": "State (two-letter)", "type": "money", "default": "CA"},
     ]},
]

# --- municipal bonds ------------------------------------------------------
CALCULATORS += [
    {"id": "muni_yield", "domain": "muni", "title": "Taxable-equivalent yield",
     "summary": "What a taxable bond must yield to match this municipal one, counting "
                "state tax and the 3.8% surtax the exemption also avoids.",
     "fields": [
         {"name": "municipal_yield", "label": "Municipal yield (e.g. 0.035)",
          "type": "money", "default": "0.035"},
         {"name": "federal_rate", "label": "Federal marginal rate", "type": "money",
          "default": "0.37"},
         {"name": "state_rate", "label": "State marginal rate", "type": "money",
          "default": "0.093"},
         {"name": "state_exempt", "label": "Exempt in the holder's own state",
          "type": "select", "options": ["yes", "no"], "default": "yes"},
         {"name": "niit_applies", "label": "Subject to the 3.8% surtax",
          "type": "select", "options": ["yes", "no"], "default": "yes"},
         {"name": "private_activity", "label": "Private activity bond",
          "type": "select", "options": ["no", "yes"], "default": "no"},
         {"name": "amt_applies", "label": "Taxpayer is in AMT", "type": "select",
          "options": ["no", "yes"], "default": "no"},
         {"name": "issue_year", "label": "Year the bond was issued (blank = unknown)",
          "type": "integer", "default": ""},
     ]},
    {"id": "muni_discount", "domain": "muni", "title": "Market discount and de minimis",
     "summary": "Whether a discount bond's accretion is capital gain or ordinary "
                "income. Above the threshold a tax-exempt bond carries the top rate.",
     "fields": [
         {"name": "purchase_price", "label": "Purchase price", "type": "money",
          "default": "950"},
         {"name": "redemption_price", "label": "Redemption price at maturity",
          "type": "money", "default": "1000"},
         {"name": "years_to_maturity", "label": "Complete years to maturity",
          "type": "integer", "default": "10"},
     ]},
    {"id": "muni_premium", "domain": "muni", "title": "Bond premium amortization",
     "summary": "Mandatory on a tax-exempt bond, with no deduction. Basis falls to "
                "the redemption price, so there is no loss at maturity.",
     "fields": [
         {"name": "purchase_price", "label": "Purchase price", "type": "money",
          "default": "1050"},
         {"name": "redemption_price", "label": "Redemption price at maturity",
          "type": "money", "default": "1000"},
         {"name": "years_to_maturity", "label": "Complete years to maturity",
          "type": "integer", "default": "10"},
     ]},
]

CALCULATOR_IDS = tuple(c["id"] for c in CALCULATORS)
DOMAINS = tuple(dict.fromkeys(c["domain"] for c in CALCULATORS))


def ids_for(domain: str) -> tuple:
    return tuple(c["id"] for c in CALCULATORS if c["domain"] == domain)


def spec(calc_id: str) -> Dict[str, Any]:
    found = next((c for c in CALCULATORS if c["id"] == calc_id), None)
    if found is None:
        raise KeyError(calc_id)
    return found


def input_schema(calc_id: str) -> Dict[str, Any]:
    """The MCP inputSchema for one calculator, derived from its fields.

    Money arrives as a string. A float cannot represent a cent exactly, and the
    money type raises rather than accept one, so the schema never invites a
    caller to send a number that would be refused downstream.
    """
    properties: Dict[str, Any] = {}
    for field in spec(calc_id)["fields"]:
        if field["type"] == "select":
            properties[field["name"]] = {"type": "string", "enum": list(field["options"]),
                                         "description": field["label"]}
        else:
            properties[field["name"]] = {
                "type": "string",
                "description": field["label"] + " (decimal string, e.g. \"185000.00\")",
            }
    return {"type": "object", "properties": properties, "additionalProperties": False}


def run(calc_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch to the engine. Unknown fields are ignored, not trusted."""
    allowed = {f["name"] for f in spec(calc_id)["fields"]}
    clean = {k: str(v).strip() for k, v in values.items() if k in allowed}
    status = clean.get("status", "MFJ").upper()

    def money(name: str, fallback: str = "0") -> str:
        value = clean.get(name, "")
        return value if value else fallback

    def blank(name: str) -> Optional[str]:
        """A field left empty means "do not apply this", not zero."""
        value = clean.get(name, "")
        return value or None

    def count(name: str, fallback: int = 1) -> int:
        value = clean.get(name, "")
        if not value:
            return fallback
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{name}: expected a whole number, got {value!r}")

    defaults = {f["name"]: str(f.get("default", "")) for f in spec(calc_id)["fields"]}

    def flag(name: str) -> bool:
        """A boolean field, falling back to the spec's own declared default.

        Hardcoding "no" here meant an omitted field silently took the opposite
        of what the form shows. `state_exempt` defaults to yes, so a caller who
        did not know the field existed got the out-of-state answer — a
        different and worse number, with nothing to signal it.
        """
        return clean.get(name, defaults.get(name, "no")).lower() in {"yes", "true", "1"}

    if calc_id == "federal":
        result = federal.summarize_federal(
            money("income"), status, long_term_gain=money("gain"),
            deduction=blank("deduction"), net_investment_income=money("nii"))
    elif calc_id == "capgains":
        result = federal.calculate_capital_gain_tax(money("gain"), money("ordinary"), status)
    elif calc_id == "niit":
        result = federal.calculate_niit(money("nii"), money("magi"), status)
    elif calc_id == "amt":
        result = federal.calculate_amt(money("amti"), money("regular_tax"), status,
                                       net_capital_gain=money("gain"))
    elif calc_id == "qbi":
        result = federal.calculate_qbi_deduction(
            money("qbi"), money("taxable_income"), status, w2_wages=money("wages"),
            ubia=money("ubia"), is_sstb=flag("sstb"))
    elif calc_id == "loss":
        result = federal.calculate_capital_loss_deduction(money("loss"), status)

    elif calc_id == "estate_tax":
        result = estate.calculate_estate_tax(
            money("gross_estate"), deductions=money("deductions"),
            lifetime_taxable_gifts=money("lifetime_taxable_gifts"),
            dsue_amount=money("dsue_amount"))
    elif calc_id == "gift_exclusion":
        result = estate.calculate_gift_exclusion(
            money("gift_amount"), recipients=count("recipients"),
            donors=count("donors"), noncitizen_spouse=flag("noncitizen_spouse"))
    elif calc_id == "fiduciary_tax":
        result = estate.calculate_fiduciary_income_tax(
            money("ordinary_income"), long_term_gain=money("long_term_gain"))

    elif calc_id == "feie":
        result = international.calculate_foreign_earned_income_exclusion(
            money("foreign_earned_income"), qualifying_days=count("qualifying_days", 365),
            housing_expenses=money("housing_expenses"))
    elif calc_id == "foreign_gift":
        result = international.check_foreign_gift_reporting(
            money("amount_received"), source=clean.get("source", "individual"))

    elif calc_id == "section_179":
        result = business.calculate_section_179(
            money("cost_placed_in_service"), elected_amount=blank("elected_amount"),
            business_taxable_income=blank("business_taxable_income"))
    elif calc_id == "entity_comparison":
        result = business.compare_entity_tax(
            money("business_income"), money("owner_marginal_rate"),
            distribution_fraction=money("distribution_fraction", "1.00"),
            qualified_dividend_rate=money("qualified_dividend_rate", "0.20"),
            passthrough_deduction=money("passthrough_deduction"))

    elif calc_id == "filing_penalty":
        result = compliance.calculate_filing_penalty(
            money("unpaid_tax"), days_late=count("days_late", 0), filed=flag("filed"))
    elif calc_id == "entity_penalty":
        result = compliance.calculate_entity_filing_penalty(
            clean.get("entity_type", "partnership"), owners=count("owners"),
            months_late=count("months_late", 0))
    elif calc_id == "safe_harbour":
        result = compliance.calculate_estimated_tax_safe_harbour(
            money("prior_year_tax"), money("current_year_projected_tax"),
            money("prior_year_agi"),
            withholding_and_payments=money("withholding_and_payments"),
            filing_status=status)

    elif calc_id == "muni_yield":
        result = municipal.calculate_taxable_equivalent_yield(
            money("municipal_yield"), money("federal_rate"),
            state_rate=money("state_rate"), niit_applies=flag("niit_applies"),
            state_exempt=flag("state_exempt"),
            private_activity=flag("private_activity"), amt_applies=flag("amt_applies"),
            issue_year=(count("issue_year", 0) or None))
    elif calc_id == "muni_discount":
        result = municipal.calculate_market_discount(
            money("purchase_price"), money("redemption_price"),
            years_to_maturity=count("years_to_maturity", 0))
    elif calc_id == "muni_premium":
        result = municipal.calculate_bond_premium(
            money("purchase_price"), money("redemption_price"),
            years_to_maturity=count("years_to_maturity", 1))

    elif calc_id == "salt_cap":
        result = salt.calculate_salt_cap(money("state_and_local_tax_paid"),
                                         money("magi"), status)
    elif calc_id == "state_coverage":
        # Returns data, not a result object: the answer is a boundary.
        return salt.state_coverage(clean.get("state", ""))
    else:
        raise KeyError(calc_id)
    return result.to_json()
