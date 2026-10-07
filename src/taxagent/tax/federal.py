"""Federal income tax calculations.

Every rate and threshold is read from the year's reviewed income pack. The
notable thing this module gets right, and which naive implementations usually
get wrong: **net capital gain is stacked on top of ordinary income and split
across the 0/15/20 bands**. Taxing the whole gain at the rate its top dollar
reaches overstates the tax whenever a gain straddles a threshold, which is the
common case for the taxpayers who ask.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..decisions.rule_registry import IncomeRules, RulePackError, income_rules
from ..domain import codes
from ..domain.money import Money, parse_decimal
from .bands import Band, apply_bands

FILING_STATUSES = ("SINGLE", "MFJ", "MFS", "HOH")


def _check_status(status: str) -> str:
    upper = str(status).upper()
    if upper not in FILING_STATUSES:
        raise RulePackError(
            codes.WRONG_RULE_YEAR,
            f"unknown filing status {status!r}; expected one of {', '.join(FILING_STATUSES)}")
    return upper


@dataclass(frozen=True)
class OrdinaryTax:
    taxable_income: Money
    tax: Money
    bands: Tuple[Band, ...]
    marginal_rate: Decimal
    bracket_ceiling: Optional[Money]
    headroom_in_bracket: Optional[Money]

    def to_json(self) -> Dict[str, Any]:
        return {
            "taxable_income": self.taxable_income.to_json(),
            "tax": self.tax.to_json(),
            "marginal_rate": f"{self.marginal_rate * 100:.0f}%",
            "bracket_ceiling": (self.bracket_ceiling.to_json()
                                if self.bracket_ceiling else None),
            "headroom_in_bracket": (self.headroom_in_bracket.to_json()
                                    if self.headroom_in_bracket else None),
            "bands": [b.to_json() for b in self.bands],
        }


def calculate_ordinary_tax(taxable_income, filing_status: str = "MFJ",
                           tax_year: int = 2026,
                           rules: Optional[IncomeRules] = None) -> OrdinaryTax:
    """Tax on ordinary taxable income, band by band."""
    status = _check_status(filing_status)
    rules = rules or income_rules(tax_year)
    amount = max(Decimal("0"), parse_decimal(taxable_income, "taxable_income"))
    bands = rules.for_status(rules.ordinary_brackets, status)
    detail, total, marginal, ceiling = apply_bands(amount, bands)
    headroom = None if ceiling is None else Money(max(Decimal("0"), ceiling - amount))
    return OrdinaryTax(
        taxable_income=Money(amount), tax=Money(total).quantized(), bands=tuple(detail),
        marginal_rate=marginal,
        bracket_ceiling=None if ceiling is None else Money(ceiling),
        headroom_in_bracket=headroom)


@dataclass(frozen=True)
class CapitalGainTax:
    net_long_term_gain: Money
    ordinary_taxable_income: Money
    tax: Money
    bands: Tuple[Band, ...]
    blended_rate: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "net_long_term_gain": self.net_long_term_gain.to_json(),
            "ordinary_taxable_income": self.ordinary_taxable_income.to_json(),
            "tax": self.tax.to_json(),
            "blended_rate": self.blended_rate,
            "bands": [b.to_json() for b in self.bands],
        }


def calculate_capital_gain_tax(net_long_term_gain, ordinary_taxable_income,
                               filing_status: str = "MFJ", tax_year: int = 2026,
                               rules: Optional[IncomeRules] = None) -> CapitalGainTax:
    """Preferential-rate tax on net long-term gain, stacked on ordinary income.

    The gain sits on top of ordinary taxable income, so it is split across the
    0/15/20 bands rather than taxed wholly at the rate its top dollar reaches.
    """
    status = _check_status(filing_status)
    rules = rules or income_rules(tax_year)
    gain = max(Decimal("0"), parse_decimal(net_long_term_gain, "net_long_term_gain"))
    floor = max(Decimal("0"), parse_decimal(ordinary_taxable_income, "ordinary_taxable_income"))
    bands = rules.for_status(rules.capital_gain_brackets, status)
    detail, total, _, _ = apply_bands(gain, bands, floor=floor)
    blended = (total / gain * 100) if gain > 0 else Decimal("0")
    return CapitalGainTax(
        net_long_term_gain=Money(gain), ordinary_taxable_income=Money(floor),
        tax=Money(total).quantized(), bands=tuple(detail),
        blended_rate=f"{blended.quantize(Decimal('0.01'))}%")


@dataclass(frozen=True)
class NiitResult:
    net_investment_income: Money
    magi: Money
    threshold: Money
    amount_subject: Money
    tax: Money

    def to_json(self) -> Dict[str, Any]:
        return {"net_investment_income": self.net_investment_income.to_json(),
                "magi": self.magi.to_json(), "threshold": self.threshold.to_json(),
                "amount_subject": self.amount_subject.to_json(), "tax": self.tax.to_json()}


def calculate_niit(net_investment_income, magi, filing_status: str = "MFJ",
                   tax_year: int = 2026,
                   rules: Optional[IncomeRules] = None) -> NiitResult:
    """Net investment income tax: 3.8% of the lesser of NII or MAGI excess."""
    status = _check_status(filing_status)
    rules = rules or income_rules(tax_year)
    nii = max(Decimal("0"), parse_decimal(net_investment_income, "net_investment_income"))
    magi_amount = parse_decimal(magi, "magi")
    threshold = rules.for_status(rules.niit_thresholds, status)
    excess = max(Decimal("0"), magi_amount - threshold)
    subject = min(nii, excess)
    return NiitResult(Money(nii), Money(magi_amount), Money(threshold),
                      Money(subject), Money(subject * rules.niit_rate).quantized())


@dataclass(frozen=True)
class CapitalLossResult:
    net_capital_loss: Money
    deductible_this_year: Money
    carryforward: Money
    limit: Money

    def to_json(self) -> Dict[str, Any]:
        return {"net_capital_loss": self.net_capital_loss.to_json(),
                "deductible_this_year": self.deductible_this_year.to_json(),
                "carryforward": self.carryforward.to_json(), "limit": self.limit.to_json()}


def calculate_capital_loss_deduction(net_capital_loss, filing_status: str = "MFJ",
                                     tax_year: int = 2026,
                                     rules: Optional[IncomeRules] = None) -> CapitalLossResult:
    """How much of a net capital loss is deductible now, and what carries forward."""
    status = _check_status(filing_status)
    rules = rules or income_rules(tax_year)
    loss = abs(parse_decimal(net_capital_loss, "net_capital_loss"))
    limit = rules.for_status(rules.capital_loss_limit, status)
    deductible = min(loss, limit)
    return CapitalLossResult(Money(loss), Money(deductible),
                             Money(loss - deductible), Money(limit))


@dataclass(frozen=True)
class FederalSummary:
    filing_status: str
    tax_year: int
    gross_income: Money
    deduction: Money
    ordinary: OrdinaryTax
    capital_gain: CapitalGainTax
    niit: NiitResult
    total_tax: Money
    effective_rate: str
    rule_bundle_ref: str
    rule_bundle_hash: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "filing_status": self.filing_status, "tax_year": self.tax_year,
            "gross_income": self.gross_income.to_json(),
            "deduction": self.deduction.to_json(),
            "ordinary": self.ordinary.to_json(),
            "capital_gain": self.capital_gain.to_json(),
            "niit": self.niit.to_json(),
            "total_tax": self.total_tax.to_json(),
            "effective_rate": self.effective_rate,
            "rule_bundle_ref": self.rule_bundle_ref,
            "rule_bundle_hash": self.rule_bundle_hash,
            "disclaimer": "Estimate for professional review. Not tax advice.",
        }


def summarize_federal(ordinary_income, filing_status: str = "MFJ",
                      long_term_gain="0", deduction=None, net_investment_income="0",
                      tax_year: int = 2026,
                      rules: Optional[IncomeRules] = None) -> FederalSummary:
    """Ordinary tax, preferential-rate tax on gains, and NIIT in one pass.

    This is an estimate over the inputs given. It is not a return: AMT, credits,
    QBI, state tax and phase-outs are not modelled. The federal-income-estimator
    entry in capabilities/engines.yaml records that, and records separately that
    no return-level engine exists to compose them.
    """
    status = _check_status(filing_status)
    rules = rules or income_rules(tax_year)
    gross = parse_decimal(ordinary_income, "ordinary_income")
    gain = max(Decimal("0"), parse_decimal(long_term_gain, "long_term_gain"))
    allowance = (rules.for_status(rules.standard_deduction, status)
                 if deduction is None else parse_decimal(deduction, "deduction"))

    ordinary_taxable = max(Decimal("0"), gross - allowance)
    ordinary = calculate_ordinary_tax(ordinary_taxable, status, tax_year, rules)
    capital = calculate_capital_gain_tax(gain, ordinary_taxable, status, tax_year, rules)
    magi = gross + gain
    nii = parse_decimal(net_investment_income, "net_investment_income")
    niit = calculate_niit(nii, magi, status, tax_year, rules)

    total = ordinary.tax + capital.tax + niit.tax
    base = gross + gain
    effective = (total.amount / base * 100) if base > 0 else Decimal("0")
    return FederalSummary(
        filing_status=status, tax_year=tax_year, gross_income=Money(gross),
        deduction=Money(allowance), ordinary=ordinary, capital_gain=capital, niit=niit,
        total_tax=total.quantized(),
        effective_rate=f"{effective.quantize(Decimal('0.01'))}%",
        rule_bundle_ref=rules.bundle_id, rule_bundle_hash=rules.bundle_hash)


# --- alternative minimum tax ---------------------------------------------

@dataclass(frozen=True)
class AmtResult:
    amti: Money
    statutory_exemption: Money
    exemption_lost_to_phaseout: Money
    effective_exemption: Money
    amt_base: Money
    tentative_minimum_tax: Money
    regular_tax: Money
    amt_payable: Money
    bands: Tuple[Band, ...]
    preferential_gain: Money

    @property
    def applies(self) -> bool:
        return self.amt_payable.amount > 0

    def to_json(self) -> Dict[str, Any]:
        return {
            "amti": self.amti.to_json(),
            "statutory_exemption": self.statutory_exemption.to_json(),
            "exemption_lost_to_phaseout": self.exemption_lost_to_phaseout.to_json(),
            "effective_exemption": self.effective_exemption.to_json(),
            "amt_base": self.amt_base.to_json(),
            "preferential_gain": self.preferential_gain.to_json(),
            "tentative_minimum_tax": self.tentative_minimum_tax.to_json(),
            "regular_tax": self.regular_tax.to_json(),
            "amt_payable": self.amt_payable.to_json(),
            "applies": self.applies,
            "bands": [b.to_json() for b in self.bands],
            "not_modelled": [
                "AMT credit carryforward from a prior year (Form 8801)",
                "ISO exercise basis differences beyond the AMTI figure supplied",
                "Depletion, intangible drilling and other section 57 preferences",
            ],
        }


def calculate_amt(amti, regular_tax, filing_status: str = "MFJ",
                  net_capital_gain="0", tax_year: int = 2026,
                  rules: Optional[IncomeRules] = None) -> AmtResult:
    """Alternative minimum tax: the excess of tentative minimum tax over regular tax.

    Two details that are commonly dropped and change the answer:

    * The exemption phases out at 50 cents per dollar of AMTI above the
      threshold from 2026, not 25 cents as in earlier years.
    * Net capital gain keeps its preferential rate inside the AMT calculation.
      Taxing the whole AMT base at 26/28% overstates AMT for anyone with
      significant gains, which is most people who reach AMT at all.
    """
    status = _check_status(filing_status)
    rules = rules or income_rules(tax_year)
    amt = rules.amt

    amti_amount = max(Decimal("0"), parse_decimal(amti, "amti"))
    gain = max(Decimal("0"), parse_decimal(net_capital_gain, "net_capital_gain"))
    regular = max(Decimal("0"), parse_decimal(regular_tax, "regular_tax"))

    exemption = rules.for_status(amt["exemption"], status)
    threshold = rules.for_status(amt["phaseout_threshold"], status)
    lost = max(Decimal("0"), (amti_amount - threshold) * amt["phaseout_rate"])
    lost = min(lost, exemption)
    effective_exemption = exemption - lost

    base = max(Decimal("0"), amti_amount - effective_exemption)
    # The gain cannot exceed the base it sits in.
    gain_in_base = min(gain, base)
    ordinary_base = base - gain_in_base

    breakpoint_ = rules.for_status(amt["rate_breakpoint"], status)
    bands: List[Band] = []
    at_low = min(ordinary_base, breakpoint_)
    if at_low > 0:
        bands.append(Band(amt["rate_low"], Money(at_low),
                          Money(at_low * amt["rate_low"]).quantized()))
    at_high = max(Decimal("0"), ordinary_base - breakpoint_)
    if at_high > 0:
        bands.append(Band(amt["rate_high"], Money(at_high),
                          Money(at_high * amt["rate_high"]).quantized()))
    tentative = sum((b.tax.amount for b in bands), Decimal("0"))

    if gain_in_base > 0:
        preferential = calculate_capital_gain_tax(gain_in_base, ordinary_base, status,
                                                  tax_year, rules)
        tentative += preferential.tax.amount
        bands.extend(preferential.bands)

    payable = max(Decimal("0"), tentative - regular)
    return AmtResult(
        amti=Money(amti_amount), statutory_exemption=Money(exemption),
        exemption_lost_to_phaseout=Money(lost), effective_exemption=Money(effective_exemption),
        amt_base=Money(base), tentative_minimum_tax=Money(tentative).quantized(),
        regular_tax=Money(regular), amt_payable=Money(payable).quantized(),
        bands=tuple(bands), preferential_gain=Money(gain_in_base))


# --- section 199A qualified business income ------------------------------

@dataclass(frozen=True)
class QbiResult:
    qualified_business_income: Money
    taxable_income: Money
    threshold: Money
    phase_in_range: Money
    phase_in_ratio: str
    is_sstb: bool
    tentative_deduction: Money
    wage_and_property_limit: Money
    taxable_income_limit: Money
    deduction: Money
    limit_applied: str
    notes: Tuple[str, ...] = ()

    def to_json(self) -> Dict[str, Any]:
        return {
            "qualified_business_income": self.qualified_business_income.to_json(),
            "taxable_income": self.taxable_income.to_json(),
            "threshold": self.threshold.to_json(),
            "phase_in_range": self.phase_in_range.to_json(),
            "phase_in_ratio": self.phase_in_ratio,
            "is_sstb": self.is_sstb,
            "tentative_deduction": self.tentative_deduction.to_json(),
            "wage_and_property_limit": self.wage_and_property_limit.to_json(),
            "taxable_income_limit": self.taxable_income_limit.to_json(),
            "deduction": self.deduction.to_json(),
            "limit_applied": self.limit_applied,
            "notes": list(self.notes),
        }


def calculate_qbi_deduction(qualified_business_income, taxable_income,
                            filing_status: str = "MFJ", w2_wages="0", ubia="0",
                            net_capital_gain="0", is_sstb: bool = False,
                            tax_year: int = 2026,
                            rules: Optional[IncomeRules] = None) -> QbiResult:
    """Section 199A deduction.

    `taxable_income` is taxable income **before** this deduction, which is what
    the thresholds are measured against. Three limits interact:

    * the wage-and-property limit, which phases in over the range above the
      threshold rather than switching on at it;
    * for a specified service trade or business, an applicable percentage that
      phases the whole deduction out over the same range;
    * an overall cap of 20% of taxable income less net capital gain.
    """
    status = _check_status(filing_status)
    rules = rules or income_rules(tax_year)
    qbi = rules.qbi

    income = max(Decimal("0"), parse_decimal(qualified_business_income,
                                             "qualified_business_income"))
    taxable = max(Decimal("0"), parse_decimal(taxable_income, "taxable_income"))
    wages = max(Decimal("0"), parse_decimal(w2_wages, "w2_wages"))
    property_basis = max(Decimal("0"), parse_decimal(ubia, "ubia"))
    gain = max(Decimal("0"), parse_decimal(net_capital_gain, "net_capital_gain"))

    rate = qbi["deduction_rate"]
    threshold = rules.for_status(qbi["threshold"], status)
    span = rules.for_status(qbi["phase_in_range"], status)
    excess = max(Decimal("0"), taxable - threshold)
    ratio = min(Decimal("1"), excess / span) if span > 0 else Decimal("0")
    notes: List[str] = []

    def wage_limit(w: Decimal, u: Decimal) -> Decimal:
        return max(w * qbi["w2_only_rate"],
                   w * qbi["w2_with_ubia_wage_rate"] + u * qbi["ubia_rate"])

    income_counted = income          # QBI actually taken into account
    if excess <= 0:
        tentative = income * rate
        limit = wage_limit(wages, property_basis)
        deduction = tentative
        applied = "none_below_threshold"
        if is_sstb:
            notes.append("Below the threshold an SSTB is not restricted.")
    elif ratio >= 1:
        if is_sstb:
            tentative = Decimal("0")
            limit = Decimal("0")
            deduction = Decimal("0")
            income_counted = Decimal("0")
            applied = "sstb_fully_phased_out"
            notes.append("A specified service business above the phase-in range gets no "
                         "deduction on that income.")
        else:
            tentative = income * rate
            limit = wage_limit(wages, property_basis)
            deduction = min(tentative, limit)
            applied = "wage_and_property_limit"
    else:
        applicable = Decimal("1") - ratio
        adjusted_income = income * applicable if is_sstb else income
        income_counted = adjusted_income
        adjusted_wages = wages * applicable if is_sstb else wages
        adjusted_property = property_basis * applicable if is_sstb else property_basis
        tentative = adjusted_income * rate
        limit = wage_limit(adjusted_wages, adjusted_property)
        limited = min(tentative, limit)
        # Only the excess over the limit is phased in, not the whole limit.
        deduction = tentative - (tentative - limited) * ratio
        applied = "sstb_partial_phase_out" if is_sstb else "wage_limit_phasing_in"

    taxable_income_cap = max(Decimal("0"), taxable - gain) * rate
    if deduction > taxable_income_cap:
        deduction = taxable_income_cap
        applied = "taxable_income_limit"
        notes.append("Capped at 20% of taxable income less net capital gain.")

    # The floor is measured against QBI actually taken into account. A fully
    # phased-out SSTB has none, so nothing is left for the minimum to attach to.
    # Whether the new minimum deduction can still reach such a taxpayer is not
    # settled; this takes the narrower reading and says so.
    floor = qbi["minimum_deduction"]
    if income_counted >= qbi["minimum_deduction_qbi_floor"] and deduction < floor:
        deduction = min(floor, taxable_income_cap)
        applied = "minimum_deduction"
        notes.append("Minimum deduction applied for at least $1,000 of active QBI. "
                     "Material participation is assumed, not verified.")
    elif (is_sstb and income_counted == 0
          and income >= qbi["minimum_deduction_qbi_floor"]):
        notes.append("The minimum deduction was not applied: a fully phased-out SSTB has "
                     "no QBI taken into account. Whether the minimum can still reach such "
                     "a taxpayer is unsettled — refer this to the reviewing professional.")

    return QbiResult(
        qualified_business_income=Money(income), taxable_income=Money(taxable),
        threshold=Money(threshold), phase_in_range=Money(span),
        phase_in_ratio=f"{(ratio * 100).quantize(Decimal('0.1'))}%", is_sstb=is_sstb,
        tentative_deduction=Money(tentative).quantized(),
        wage_and_property_limit=Money(limit).quantized(),
        taxable_income_limit=Money(taxable_income_cap).quantized(),
        deduction=Money(deduction).quantized(), limit_applied=applied, notes=tuple(notes))
