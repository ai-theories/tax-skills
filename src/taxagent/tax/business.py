"""Federal business-entity calculations.

Section 179 expensing, the corporate rate, and the comparison between taxing
business income inside a C corporation and passing it through. The comparison
is arithmetic over stated assumptions, not advice about entity choice: that
turns on exit plans, state tax, payroll, fringe benefits and ownership
changes, none of which are here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..decisions.rule_registry import DomainRules, RulePackError, domain_rules
from ..domain import codes
from ..domain.money import Money, parse_decimal

DOMAIN = "business"


def _rules(tax_year: int, rules: Optional[DomainRules]) -> DomainRules:
    return rules or domain_rules(DOMAIN, tax_year)


@dataclass(frozen=True)
class Section179:
    cost_placed_in_service: Money
    elected_amount: Money
    maximum: Money
    phaseout_threshold: Money
    cost_over_threshold: Money
    reduction: Money
    limit_after_phaseout: Money
    taxable_income_limit: Optional[Money]
    deduction: Money
    carryforward: Money
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "cost_placed_in_service": str(self.cost_placed_in_service.quantized().amount),
            "elected_amount": str(self.elected_amount.quantized().amount),
            "maximum": str(self.maximum.quantized().amount),
            "phaseout_threshold": str(self.phaseout_threshold.quantized().amount),
            "cost_over_threshold": str(self.cost_over_threshold.quantized().amount),
            "reduction": str(self.reduction.quantized().amount),
            "limit_after_phaseout": str(self.limit_after_phaseout.quantized().amount),
            "taxable_income_limit": (str(self.taxable_income_limit.quantized().amount)
                                     if self.taxable_income_limit else None),
            "deduction": str(self.deduction.quantized().amount),
            "carryforward": str(self.carryforward.quantized().amount),
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_section_179(cost_placed_in_service, elected_amount=None,
                          business_taxable_income=None, tax_year: int = 2026,
                          rules: Optional[DomainRules] = None) -> Section179:
    """Expensing under section 179, with both limits that apply to it.

    Two separate limits, commonly confused. The dollar limit falls one-for-one
    with spending above the threshold, so a business that buys enough loses the
    election entirely. The taxable income limit then caps the deduction at
    business income, with the excess carried forward rather than lost.
    """
    pack = _rules(tax_year, rules)
    figures = pack.get("section_179")

    cost = max(Decimal("0"), parse_decimal(cost_placed_in_service,
                                           "cost_placed_in_service"))
    elected = (cost if elected_amount is None
               else max(Decimal("0"), parse_decimal(elected_amount, "elected_amount")))
    if elected > cost:
        raise RulePackError(codes.VALIDATION_FAILED,
                            "elected amount cannot exceed the cost placed in service")

    maximum = Decimal(figures["maximum"])
    threshold = Decimal(figures["phaseout_threshold"])
    over = max(Decimal("0"), cost - threshold)
    # Dollar for dollar, not a percentage.
    reduction = min(over, maximum)
    after_phaseout = max(Decimal("0"), maximum - reduction)

    allowed = min(elected, after_phaseout)
    income_limit: Optional[Decimal] = None
    carryforward = Decimal("0")
    if business_taxable_income is not None:
        income_limit = max(Decimal("0"), parse_decimal(business_taxable_income,
                                                       "business_taxable_income"))
        if allowed > income_limit:
            carryforward = allowed - income_limit
            allowed = income_limit

    notes: List[str] = []
    if over > 0 and after_phaseout > 0:
        notes.append(
            f"Spending is ${over:,.0f} over the ${threshold:,.0f} threshold, so the dollar "
            "limit falls by the same amount. Deferring a purchase into the next year can "
            "restore the limit.")
    elif after_phaseout == 0:
        notes.append(f"Spending exceeds ${threshold:,.0f} by more than the limit itself, so "
                     "no section 179 deduction is available at all this year.")
    if carryforward > 0:
        notes.append("Limited by business taxable income. The excess carries forward "
                     "indefinitely rather than being lost.")
    if business_taxable_income is None:
        notes.append("The taxable income limit was not applied: business taxable income "
                     "was not supplied, so this is the dollar limit only.")
    notes.append("Bonus depreciation, listed-property rules, the mid-quarter convention "
                 "and recapture on later disposition are not computed here.")

    return Section179(
        cost_placed_in_service=Money(cost), elected_amount=Money(elected),
        maximum=Money(maximum), phaseout_threshold=Money(threshold),
        cost_over_threshold=Money(over), reduction=Money(reduction),
        limit_after_phaseout=Money(after_phaseout),
        taxable_income_limit=None if income_limit is None else Money(income_limit),
        deduction=Money(allowed), carryforward=Money(carryforward),
        rule_bundle_ref=pack.bundle_id, notes=notes)


@dataclass(frozen=True)
class EntityComparison:
    business_income: Money
    corporate_rate: str
    corporate_tax: Money
    after_corporate_tax: Money
    distributed: Money
    shareholder_tax_on_distribution: Money
    total_c_corporation_tax: Money
    passthrough_tax: Money
    difference: Money
    favours: str
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "business_income": str(self.business_income.quantized().amount),
            "corporate_rate": self.corporate_rate,
            "corporate_tax": str(self.corporate_tax.quantized().amount),
            "after_corporate_tax": str(self.after_corporate_tax.quantized().amount),
            "distributed": str(self.distributed.quantized().amount),
            "shareholder_tax_on_distribution":
                str(self.shareholder_tax_on_distribution.quantized().amount),
            "total_c_corporation_tax": str(self.total_c_corporation_tax.quantized().amount),
            "passthrough_tax": str(self.passthrough_tax.quantized().amount),
            "difference": str(self.difference.quantized().amount),
            "favours": self.favours,
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def compare_entity_tax(business_income, owner_marginal_rate,
                       distribution_fraction="1.00", qualified_dividend_rate="0.20",
                       passthrough_deduction="0", tax_year: int = 2026,
                       rules: Optional[DomainRules] = None) -> EntityComparison:
    """Federal tax on the same income inside a C corporation versus passed through.

    Rates are supplied by the caller rather than derived, because the owner's
    marginal rate depends on everything else on their return. The result is
    arithmetic over the assumptions given, and changes with them.
    """
    pack = _rules(tax_year, rules)
    income = max(Decimal("0"), parse_decimal(business_income, "business_income"))
    owner_rate = parse_decimal(owner_marginal_rate, "owner_marginal_rate")
    fraction = parse_decimal(distribution_fraction, "distribution_fraction")
    dividend_rate = parse_decimal(qualified_dividend_rate, "qualified_dividend_rate")
    deduction = max(Decimal("0"), parse_decimal(passthrough_deduction,
                                                "passthrough_deduction"))
    for name, value in (("owner_marginal_rate", owner_rate),
                        ("distribution_fraction", fraction),
                        ("qualified_dividend_rate", dividend_rate)):
        if not Decimal("0") <= value <= Decimal("1"):
            raise RulePackError(codes.VALIDATION_FAILED,
                                f"{name} must be a fraction between 0 and 1")

    corporate_rate = Decimal(pack.get("corporate_rate"))
    corporate_tax = income * corporate_rate
    after_corporate = income - corporate_tax
    distributed = after_corporate * fraction
    shareholder_tax = distributed * dividend_rate
    c_total = corporate_tax + shareholder_tax

    passthrough_base = max(Decimal("0"), income - deduction)
    passthrough_tax = passthrough_base * owner_rate

    difference = c_total - passthrough_tax
    favours = ("pass-through" if difference > 0
               else "C corporation" if difference < 0 else "neither")

    notes = [
        "Federal income tax only, over the rates you supplied. Self-employment tax, the "
        "net investment income tax, payroll on reasonable compensation, and state and "
        "local tax are not included, and any of them can reverse this.",
        "Retained earnings change the answer: the second layer is only paid when profit "
        "is distributed, so a corporation that reinvests defers it.",
        "Entity choice also turns on exit plans, qualified small business stock, fringe "
        "benefits, ownership restrictions and the cost of changing later. None of that "
        "is arithmetic and none of it is here.",
    ]
    if fraction < 1:
        notes.append(f"Assumes {fraction * 100:.0f}% of after-tax profit is distributed. "
                     "The rest defers its second layer rather than avoiding it.")

    return EntityComparison(
        business_income=Money(income), corporate_rate=f"{corporate_rate * 100:.0f}%",
        corporate_tax=Money(corporate_tax), after_corporate_tax=Money(after_corporate),
        distributed=Money(distributed),
        shareholder_tax_on_distribution=Money(shareholder_tax),
        total_c_corporation_tax=Money(c_total), passthrough_tax=Money(passthrough_tax),
        difference=Money(abs(difference)), favours=favours,
        rule_bundle_ref=pack.bundle_id, notes=notes)
