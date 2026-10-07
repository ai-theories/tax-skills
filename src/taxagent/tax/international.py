"""Federal international calculations: the foreign earned income exclusion,
the housing amount, and the reporting threshold for gifts from abroad.

What is deliberately absent: eligibility. The exclusion requires a tax home
abroad and either bona fide residence or physical presence, and those are
facts about where someone lived and worked. This engine computes the limit a
qualifying taxpayer could exclude and says plainly that it has not established
that they qualify.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..decisions.rule_registry import DomainRules, RulePackError, domain_rules
from ..domain import codes
from ..domain.money import Money, parse_decimal

DOMAIN = "international"
DAYS_IN_YEAR = Decimal("365")


def _rules(tax_year: int, rules: Optional[DomainRules]) -> DomainRules:
    return rules or domain_rules(DOMAIN, tax_year)


@dataclass(frozen=True)
class ForeignEarnedIncome:
    foreign_earned_income: Money
    qualifying_days: int
    proration_fraction: str
    maximum_exclusion: Money
    prorated_exclusion: Money
    income_excluded: Money
    housing_expenses: Money
    housing_base: Money
    housing_limit: Money
    housing_exclusion: Money
    total_excluded: Money
    income_remaining: Money
    rule_bundle_ref: str
    eligibility_established: bool
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "foreign_earned_income": str(self.foreign_earned_income.quantized().amount),
            "qualifying_days": self.qualifying_days,
            "proration_fraction": self.proration_fraction,
            "maximum_exclusion": str(self.maximum_exclusion.quantized().amount),
            "prorated_exclusion": str(self.prorated_exclusion.quantized().amount),
            "income_excluded": str(self.income_excluded.quantized().amount),
            "housing_expenses": str(self.housing_expenses.quantized().amount),
            "housing_base": str(self.housing_base.quantized().amount),
            "housing_limit": str(self.housing_limit.quantized().amount),
            "housing_exclusion": str(self.housing_exclusion.quantized().amount),
            "total_excluded": str(self.total_excluded.quantized().amount),
            "income_remaining": str(self.income_remaining.quantized().amount),
            "rule_bundle_ref": self.rule_bundle_ref,
            "eligibility_established": self.eligibility_established,
            "notes": list(self.notes),
        }


def calculate_foreign_earned_income_exclusion(
        foreign_earned_income, qualifying_days: int = 365, housing_expenses="0",
        tax_year: int = 2026,
        rules: Optional[DomainRules] = None) -> ForeignEarnedIncome:
    """The exclusion a qualifying taxpayer could claim, and the housing amount.

    `qualifying_days` prorates the exclusion for a partial qualifying period.
    The housing amount is the excess of housing expenses over a base of 16% of
    the exclusion, capped at 30% of it, both computed from the exclusion rather
    than stored separately so they cannot disagree with it.
    """
    pack = _rules(tax_year, rules)
    if not 0 <= qualifying_days <= 366:
        raise RulePackError(codes.VALIDATION_FAILED,
                            "qualifying_days must be between 0 and 366")

    income = max(Decimal("0"), parse_decimal(foreign_earned_income,
                                             "foreign_earned_income"))
    housing = max(Decimal("0"), parse_decimal(housing_expenses, "housing_expenses"))
    maximum = Decimal(pack.get("foreign_earned_income_exclusion"))
    fraction = Decimal(qualifying_days) / DAYS_IN_YEAR
    prorated = (maximum * fraction).quantize(Decimal("0.01"))

    excluded = min(income, prorated)
    base = (maximum * Decimal(pack.get("housing_base_fraction")) * fraction
            ).quantize(Decimal("0.01"))
    limit = (maximum * Decimal(pack.get("housing_limit_fraction")) * fraction
             ).quantize(Decimal("0.01"))
    # Housing is the excess over the base, capped, and only from income the
    # exclusion itself did not already cover.
    housing_excess = max(Decimal("0"), min(housing, limit) - base)
    housing_exclusion = min(housing_excess, max(Decimal("0"), income - excluded))

    notes: List[str] = [
        "Eligibility is NOT established here. The exclusion requires a tax home abroad "
        "and either bona fide residence for a full tax year or physical presence for "
        "330 full days in twelve months. Those are facts about where the taxpayer "
        "lived and worked, and this engine has not seen them.",
        "Excluded income cannot also generate a foreign tax credit. Claiming both on "
        "the same income is the most common error here, and the credit is often worth "
        "more in a high-tax country.",
    ]
    if qualifying_days < 365:
        notes.append(f"Prorated for {qualifying_days} qualifying days. A partial period "
                     "reduces both the exclusion and the housing figures.")
    if housing > limit:
        notes.append("Housing expenses above the limit are not excludable. Higher limits "
                     "apply in certain high-cost locations and are not in this pack.")
    notes.append("Self-employment tax is not reduced by this exclusion, and GILTI, "
                 "subpart F, treaty positions and FBAR are not computed.")

    total = excluded + housing_exclusion
    return ForeignEarnedIncome(
        foreign_earned_income=Money(income), qualifying_days=qualifying_days,
        proration_fraction=f"{fraction * 100:.1f}%",
        maximum_exclusion=Money(maximum), prorated_exclusion=Money(prorated),
        income_excluded=Money(excluded), housing_expenses=Money(housing),
        housing_base=Money(base), housing_limit=Money(limit),
        housing_exclusion=Money(housing_exclusion), total_excluded=Money(total),
        income_remaining=Money(max(Decimal("0"), income - total)),
        rule_bundle_ref=pack.bundle_id, eligibility_established=False, notes=notes)


@dataclass(frozen=True)
class ForeignGiftReporting:
    amount_received: Money
    threshold: Money
    reporting_required: bool
    source: str
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "amount_received": str(self.amount_received.quantized().amount),
            "threshold": str(self.threshold.quantized().amount),
            "reporting_required": self.reporting_required,
            "source": self.source,
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def check_foreign_gift_reporting(amount_received, source: str = "individual",
                                 tax_year: int = 2026,
                                 rules: Optional[DomainRules] = None) -> ForeignGiftReporting:
    """Whether a gift from a foreign person crosses the reporting threshold.

    Reporting is not taxation: a gift from abroad is generally not income. The
    penalty for not reporting is nonetheless substantial, which is why this is
    worth checking separately from any tax question.
    """
    pack = _rules(tax_year, rules)
    kind = str(source).lower()
    if kind not in {"individual", "estate", "corporation", "partnership"}:
        raise RulePackError(
            codes.VALIDATION_FAILED,
            "source must be one of: individual, estate, corporation, partnership")

    amount = max(Decimal("0"), parse_decimal(amount_received, "amount_received"))
    threshold = Decimal(pack.get("foreign_gift_reporting_threshold"))
    # The published inflation-adjusted figure governs gifts from foreign
    # corporations and partnerships. Gifts from foreign individuals and estates
    # use the statutory $100,000, which section 6039F does not index.
    applicable = threshold if kind in {"corporation", "partnership"} else Decimal("100000")

    notes = [
        "Reporting, not tax. A gift from a foreign person is generally not income to "
        "the recipient; the obligation is to disclose it on Form 3520.",
        "Aggregate all gifts from related foreign donors in the year before comparing "
        "with the threshold, not each gift separately.",
    ]
    if kind in {"corporation", "partnership"}:
        notes.append("The threshold for gifts from foreign corporations and partnerships "
                     "is indexed for inflation; the one for individuals and estates is "
                     "the flat statutory $100,000.")
    return ForeignGiftReporting(
        amount_received=Money(amount), threshold=Money(applicable),
        reporting_required=amount > applicable, source=kind,
        rule_bundle_ref=pack.bundle_id, notes=notes)
