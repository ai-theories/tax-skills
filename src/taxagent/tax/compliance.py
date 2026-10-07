"""Federal filing-compliance calculations: late filing, late payment,
entity-level late filing, and the estimated-tax safe harbour.

Interest is deliberately not computed. The underpayment rate is set quarterly
and compounds daily, so a figure recorded in a pack would be stale within
three months. Quoting a stale interest rate in something that looks like a
penalty computation is worse than declining to compute it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..decisions.rule_registry import DomainRules, RulePackError, domain_rules
from ..domain import codes
from ..domain.money import Money, parse_decimal

DOMAIN = "compliance"


def _rules(tax_year: int, rules: Optional[DomainRules]) -> DomainRules:
    return rules or domain_rules(DOMAIN, tax_year)


@dataclass(frozen=True)
class FilingPenalty:
    unpaid_tax: Money
    months_late: int
    days_late: int
    failure_to_file: Money
    failure_to_pay: Money
    combined_month_offset_applied: bool
    minimum_applied: bool
    total_penalty: Money
    interest_computed: bool
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "unpaid_tax": str(self.unpaid_tax.quantized().amount),
            "months_late": self.months_late,
            "days_late": self.days_late,
            "failure_to_file": str(self.failure_to_file.quantized().amount),
            "failure_to_pay": str(self.failure_to_pay.quantized().amount),
            "combined_month_offset_applied": self.combined_month_offset_applied,
            "minimum_applied": self.minimum_applied,
            "total_penalty": str(self.total_penalty.quantized().amount),
            "interest_computed": self.interest_computed,
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_filing_penalty(unpaid_tax, days_late: int, filed: bool = False,
                             tax_year: int = 2026,
                             rules: Optional[DomainRules] = None) -> FilingPenalty:
    """Failure-to-file and failure-to-pay additions to tax.

    Both run monthly on any part of a month, which is why a return one day late
    already carries a full month. Where both apply in the same month the
    failure-to-file rate is reduced by the failure-to-pay rate, so the combined
    rate is 5% a month and not 5.5%.

    `filed` true means the return was filed on time but the tax was not paid:
    only the failure-to-pay addition runs.
    """
    pack = _rules(tax_year, rules)
    if days_late < 0:
        raise RulePackError(codes.VALIDATION_FAILED, "days_late cannot be negative")

    file_rules = pack.get("failure_to_file")
    pay_rules = pack.get("failure_to_pay")
    unpaid = max(Decimal("0"), parse_decimal(unpaid_tax, "unpaid_tax"))
    # Any part of a month counts as a whole month.
    months = int(math.ceil(days_late / 30)) if days_late else 0

    pay_rate = Decimal(pay_rules["monthly_rate"])
    pay_months = min(months, int(Decimal(pay_rules["maximum_rate"]) / pay_rate))
    failure_to_pay = unpaid * pay_rate * pay_months

    failure_to_file = Decimal("0")
    offset_applied = False
    minimum_applied = False
    if not filed:
        file_rate = Decimal(file_rules["monthly_rate"])
        net_rate = file_rate - pay_rate
        offset_applied = True
        file_months = min(months, int(Decimal(file_rules["maximum_rate"]) / file_rate))
        failure_to_file = unpaid * net_rate * file_months
        if days_late > 60:
            floor = min(Decimal(file_rules["minimum_after_60_days"]), unpaid)
            if failure_to_file < floor:
                failure_to_file = floor
                minimum_applied = True

    notes: List[str] = [
        "Interest is NOT included. The underpayment rate is set quarterly and compounds "
        "daily, so it is not in this pack and this figure is penalties only.",
        "Any part of a month counts as a whole month, so a return one day late already "
        "carries a full month of both additions.",
    ]
    if offset_applied:
        notes.append("Where both additions run in the same month, failure-to-file is "
                     "reduced by failure-to-pay: the combined rate is 5% a month, not "
                     "5.5%.")
    if minimum_applied:
        notes.append("More than 60 days late, so the minimum addition applies: the lesser "
                     "of the statutory floor or 100% of the tax due.")
    if filed:
        notes.append("Filed on time, so only the failure-to-pay addition runs. Filing on "
                     "time without paying costs a tenth of the monthly rate of not "
                     "filing at all.")
    notes.append("Reasonable cause, first-time abatement and the fraud rate are not "
                 "applied here; any of them changes the result.")

    return FilingPenalty(
        unpaid_tax=Money(unpaid), months_late=months, days_late=days_late,
        failure_to_file=Money(failure_to_file), failure_to_pay=Money(failure_to_pay),
        combined_month_offset_applied=offset_applied, minimum_applied=minimum_applied,
        total_penalty=Money(failure_to_file + failure_to_pay), interest_computed=False,
        rule_bundle_ref=pack.bundle_id, notes=notes)


@dataclass(frozen=True)
class EntityFilingPenalty:
    entity_type: str
    owners: int
    months_late: int
    per_owner_month: Money
    months_charged: int
    penalty: Money
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "entity_type": self.entity_type, "owners": self.owners,
            "months_late": self.months_late,
            "per_owner_month": str(self.per_owner_month.quantized().amount),
            "months_charged": self.months_charged,
            "penalty": str(self.penalty.quantized().amount),
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_entity_filing_penalty(entity_type: str, owners: int, months_late: int,
                                    tax_year: int = 2026,
                                    rules: Optional[DomainRules] = None
                                    ) -> EntityFilingPenalty:
    """Late filing for a partnership or S corporation.

    This penalty does not depend on tax owed. A partnership that owes nothing
    and files four months late with eight partners still owes thousands, which
    is the part people are most often surprised by.
    """
    pack = _rules(tax_year, rules)
    kind = str(entity_type).lower()
    if kind not in {"partnership", "s_corporation"}:
        raise RulePackError(codes.VALIDATION_FAILED,
                            "entity_type must be 'partnership' or 's_corporation'")
    if owners < 1:
        raise RulePackError(codes.VALIDATION_FAILED, "owners must be at least 1")
    if months_late < 0:
        raise RulePackError(codes.VALIDATION_FAILED, "months_late cannot be negative")

    figures = pack.get("partnership_late_filing" if kind == "partnership"
                       else "s_corporation_late_filing")
    per_owner = Decimal(figures["per_partner_month" if kind == "partnership"
                                else "per_shareholder_month"])
    cap = int(figures["maximum_months"])
    charged = min(months_late, cap)

    notes = [
        "This penalty is charged per owner per month and does not depend on tax owed. "
        "An entity with no taxable income still owes it.",
        f"Capped at {cap} months.",
        "Relief is often available for a small partnership that filed late but reported "
        "everything correctly; whether it applies is not decided here.",
    ]
    return EntityFilingPenalty(
        entity_type=kind, owners=owners, months_late=months_late,
        per_owner_month=Money(per_owner), months_charged=charged,
        penalty=Money(per_owner * owners * charged),
        rule_bundle_ref=pack.bundle_id, notes=notes)


@dataclass(frozen=True)
class SafeHarbour:
    prior_year_tax: Money
    current_year_projected_tax: Money
    prior_year_agi: Money
    high_income: bool
    prior_year_requirement: Money
    current_year_requirement: Money
    required_payment: Money
    withholding_and_payments: Money
    shortfall: Money
    protected: bool
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "prior_year_tax": str(self.prior_year_tax.quantized().amount),
            "current_year_projected_tax":
                str(self.current_year_projected_tax.quantized().amount),
            "prior_year_agi": str(self.prior_year_agi.quantized().amount),
            "high_income": self.high_income,
            "prior_year_requirement": str(self.prior_year_requirement.quantized().amount),
            "current_year_requirement":
                str(self.current_year_requirement.quantized().amount),
            "required_payment": str(self.required_payment.quantized().amount),
            "withholding_and_payments":
                str(self.withholding_and_payments.quantized().amount),
            "shortfall": str(self.shortfall.quantized().amount),
            "protected": self.protected,
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_estimated_tax_safe_harbour(prior_year_tax, current_year_projected_tax,
                                         prior_year_agi, withholding_and_payments="0",
                                         filing_status: str = "MFJ", tax_year: int = 2026,
                                         rules: Optional[DomainRules] = None) -> SafeHarbour:
    """The smallest payment that avoids an underpayment penalty.

    Two routes, and the taxpayer may take whichever is smaller: a percentage of
    the current year's tax, or a percentage of last year's. The prior-year
    route is the useful one precisely when income jumps, because last year's
    figure is already known and this year's is not.
    """
    pack = _rules(tax_year, rules)
    figures = pack.get("estimated_tax_safe_harbour")

    prior = max(Decimal("0"), parse_decimal(prior_year_tax, "prior_year_tax"))
    current = max(Decimal("0"), parse_decimal(current_year_projected_tax,
                                              "current_year_projected_tax"))
    agi = max(Decimal("0"), parse_decimal(prior_year_agi, "prior_year_agi"))
    paid = max(Decimal("0"), parse_decimal(withholding_and_payments,
                                           "withholding_and_payments"))

    threshold = Decimal(figures["high_income_agi_threshold_mfs"]
                        if str(filing_status).upper() == "MFS"
                        else figures["high_income_agi_threshold"])
    high = agi > threshold
    prior_fraction = Decimal(figures["prior_year_fraction_high_income"] if high
                             else figures["prior_year_fraction"])
    prior_requirement = prior * prior_fraction
    current_requirement = current * Decimal(figures["current_year_fraction"])
    required = min(prior_requirement, current_requirement)
    shortfall = max(Decimal("0"), required - paid)

    notes: List[str] = [
        "The smaller of the two routes governs, and the prior-year route is the one that "
        "protects a year when income jumps: last year's tax is already known.",
    ]
    if high:
        notes.append(f"Prior-year AGI is over ${threshold:,.0f}, so the prior-year route "
                     f"requires {prior_fraction * 100:.0f}% of last year's tax rather "
                     "than 100%.")
    notes.append("Payments must also be timely by quarter. Paying the whole amount in "
                 "the fourth quarter can still incur a penalty for the earlier ones, "
                 "unless the annualised income method applies.")
    notes.append("Withholding is treated as paid evenly across the year whenever it was "
                 "actually withheld, which is why increasing withholding late in the "
                 "year can fix an earlier shortfall and an estimated payment cannot.")

    return SafeHarbour(
        prior_year_tax=Money(prior), current_year_projected_tax=Money(current),
        prior_year_agi=Money(agi), high_income=high,
        prior_year_requirement=Money(prior_requirement),
        current_year_requirement=Money(current_requirement),
        required_payment=Money(required), withholding_and_payments=Money(paid),
        shortfall=Money(shortfall), protected=shortfall == 0,
        rule_bundle_ref=pack.bundle_id, notes=notes)
