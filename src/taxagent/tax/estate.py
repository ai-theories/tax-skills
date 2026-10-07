"""Federal estate, gift and fiduciary income calculations.

Every figure comes from a reviewed pack. The engine computes what the statute
computes and refuses the rest: whether property belongs in the gross estate,
whether a transfer is complete, whether a trust is a grantor trust and what a
closely held interest is worth are all facts, and none of them are here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..decisions.rule_registry import DomainRules, RulePackError, domain_rules
from ..domain import codes
from ..domain.money import Money, parse_decimal
from .bands import Band, apply_bands

DOMAIN = "estate"


def _rules(tax_year: int, rules: Optional[DomainRules]) -> DomainRules:
    return rules or domain_rules(DOMAIN, tax_year)


@dataclass(frozen=True)
class EstateTax:
    gross_estate: Money
    deductions: Money
    taxable_estate: Money
    lifetime_gifts_added_back: Money
    exclusion_available: Money
    exclusion_used_by_gifts: Money
    taxable_after_exclusion: Money
    tentative_tax: Money
    credit_for_exclusion: Money
    estate_tax: Money
    bands: List[Band]
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "gross_estate": str(self.gross_estate.quantized().amount),
            "deductions": str(self.deductions.quantized().amount),
            "taxable_estate": str(self.taxable_estate.quantized().amount),
            "lifetime_gifts_added_back": str(self.lifetime_gifts_added_back.quantized().amount),
            "exclusion_available": str(self.exclusion_available.quantized().amount),
            "exclusion_used_by_gifts": str(self.exclusion_used_by_gifts.quantized().amount),
            "taxable_after_exclusion": str(self.taxable_after_exclusion.quantized().amount),
            "tentative_tax": str(self.tentative_tax.quantized().amount),
            "credit_for_exclusion": str(self.credit_for_exclusion.quantized().amount),
            "estate_tax": str(self.estate_tax.quantized().amount),
            "bands": [b.to_json() for b in self.bands],
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_estate_tax(gross_estate, deductions="0", lifetime_taxable_gifts="0",
                         dsue_amount="0", tax_year: int = 2026,
                         rules: Optional[DomainRules] = None) -> EstateTax:
    """Federal estate tax under the unified credit.

    The statute taxes the estate plus lifetime taxable gifts, then credits the
    tax on the exclusion. Computing tax on the estate alone and subtracting an
    exclusion gives a different, wrong answer whenever gifts have been made,
    because the gifts push the estate into higher brackets.

    `dsue_amount` is a deceased spouse's unused exclusion. It is taken as given:
    portability requires a timely filed return on the first death, and whether
    one was filed is a fact this engine cannot check.
    """
    pack = _rules(tax_year, rules)
    schedule = [(Decimal(b["rate"]), None if b["up_to"] is None else Decimal(b["up_to"]))
                for b in pack.get("estate_gift_rate_schedule")]

    gross = max(Decimal("0"), parse_decimal(gross_estate, "gross_estate"))
    allowed = max(Decimal("0"), parse_decimal(deductions, "deductions"))
    gifts = max(Decimal("0"), parse_decimal(lifetime_taxable_gifts, "lifetime_taxable_gifts"))
    dsue = max(Decimal("0"), parse_decimal(dsue_amount, "dsue_amount"))

    taxable_estate = max(Decimal("0"), gross - allowed)
    basic = Decimal(pack.get("basic_exclusion_amount"))
    exclusion = basic + dsue

    # Tax the estate and the lifetime gifts together, then credit the exclusion.
    base = taxable_estate + gifts
    bands, tentative, _, _ = apply_bands(base, schedule)
    _, credit_base, _, _ = apply_bands(min(exclusion, base), schedule)

    tax = max(Decimal("0"), tentative - credit_base)
    exclusion_used_by_gifts = min(gifts, exclusion)
    after_exclusion = max(Decimal("0"), base - exclusion)

    notes: List[str] = []
    if gifts > 0:
        notes.append(
            "Lifetime taxable gifts are added to the taxable estate before the rate "
            "schedule is applied, so they raise the marginal rate on the estate itself.")
    if dsue > 0:
        notes.append(
            "The deceased spouse's unused exclusion is taken as given. Portability "
            "requires a timely filed return on the first death; whether one was filed "
            "is not checked here.")
    if tax == 0 and base > 0:
        notes.append("Within the exclusion, so no federal estate tax. A return may still "
                     "be required, and state estate or inheritance tax is not computed.")
    notes.append("Federal only. State estate and inheritance taxes, generation-skipping "
                 "transfer tax and valuation discounts are not computed.")

    return EstateTax(
        gross_estate=Money(gross), deductions=Money(allowed),
        taxable_estate=Money(taxable_estate), lifetime_gifts_added_back=Money(gifts),
        exclusion_available=Money(exclusion),
        exclusion_used_by_gifts=Money(exclusion_used_by_gifts),
        taxable_after_exclusion=Money(after_exclusion),
        tentative_tax=Money(tentative), credit_for_exclusion=Money(credit_base),
        estate_tax=Money(tax), bands=bands,
        rule_bundle_ref=pack.bundle_id, notes=notes)


@dataclass(frozen=True)
class GiftExclusion:
    gift_amount: Money
    recipients: int
    donors: int
    annual_exclusion_per_recipient: Money
    total_annual_exclusion: Money
    covered_by_annual_exclusion: Money
    taxable_gift: Money
    exclusion_is_noncitizen_spouse: bool
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "gift_amount": str(self.gift_amount.quantized().amount),
            "recipients": self.recipients,
            "donors": self.donors,
            "annual_exclusion_per_recipient":
                str(self.annual_exclusion_per_recipient.quantized().amount),
            "total_annual_exclusion": str(self.total_annual_exclusion.quantized().amount),
            "covered_by_annual_exclusion":
                str(self.covered_by_annual_exclusion.quantized().amount),
            "taxable_gift": str(self.taxable_gift.quantized().amount),
            "exclusion_is_noncitizen_spouse": self.exclusion_is_noncitizen_spouse,
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_gift_exclusion(gift_amount, recipients: int = 1, donors: int = 1,
                             noncitizen_spouse: bool = False, tax_year: int = 2026,
                             rules: Optional[DomainRules] = None) -> GiftExclusion:
    """How much of a gift the annual exclusion covers, and what remains taxable.

    `donors` of 2 is gift splitting: a married couple may treat a gift as made
    half by each, doubling the exclusion. It requires consent on a gift tax
    return, which this engine notes rather than assumes.
    """
    pack = _rules(tax_year, rules)
    if recipients < 1 or donors < 1:
        raise RulePackError(codes.VALIDATION_FAILED,
                            "recipients and donors must each be at least 1")
    if donors > 2:
        raise RulePackError(codes.VALIDATION_FAILED,
                            "a gift can be split between at most two spouses")

    amount = max(Decimal("0"), parse_decimal(gift_amount, "gift_amount"))
    per_recipient = Decimal(pack.get("noncitizen_spouse_gift_exclusion")
                            if noncitizen_spouse
                            else pack.get("annual_gift_exclusion"))
    if noncitizen_spouse and (recipients != 1 or donors != 1):
        raise RulePackError(
            codes.VALIDATION_FAILED,
            "the non-citizen spouse exclusion applies to gifts to one spouse from one "
            "donor; it is not multiplied by recipients or split between donors")

    total = per_recipient * recipients * donors
    covered = min(amount, total)

    notes: List[str] = []
    if donors == 2:
        notes.append("Gift splitting assumed. It requires both spouses to consent on a "
                     "gift tax return; the exclusion is not doubled automatically.")
    if noncitizen_spouse:
        notes.append("The unlimited marital deduction does not apply to a spouse who is "
                     "not a US citizen, which is why a separate annual figure exists.")
    if amount > total:
        notes.append("The excess is a taxable gift. It is normally sheltered by the "
                     "lifetime exclusion rather than taxed, but it consumes that "
                     "exclusion and a return is required.")
    notes.append("Present-interest gifts only. A gift of a future interest does not "
                 "qualify for the annual exclusion whatever its size.")

    return GiftExclusion(
        gift_amount=Money(amount), recipients=recipients, donors=donors,
        annual_exclusion_per_recipient=Money(per_recipient),
        total_annual_exclusion=Money(total), covered_by_annual_exclusion=Money(covered),
        taxable_gift=Money(max(Decimal("0"), amount - covered)),
        exclusion_is_noncitizen_spouse=noncitizen_spouse,
        rule_bundle_ref=pack.bundle_id, notes=notes)


@dataclass(frozen=True)
class FiduciaryTax:
    taxable_income: Money
    ordinary_income: Money
    long_term_gain: Money
    ordinary_tax: Money
    capital_gain_tax: Money
    total_tax: Money
    ordinary_bands: List[Band]
    gain_bands: List[Band]
    top_rate_reached_at: Money
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "taxable_income": str(self.taxable_income.quantized().amount),
            "ordinary_income": str(self.ordinary_income.quantized().amount),
            "long_term_gain": str(self.long_term_gain.quantized().amount),
            "ordinary_tax": str(self.ordinary_tax.quantized().amount),
            "capital_gain_tax": str(self.capital_gain_tax.quantized().amount),
            "total_tax": str(self.total_tax.quantized().amount),
            "ordinary_bands": [b.to_json() for b in self.ordinary_bands],
            "gain_bands": [b.to_json() for b in self.gain_bands],
            "top_rate_reached_at": str(self.top_rate_reached_at.quantized().amount),
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_fiduciary_income_tax(ordinary_income, long_term_gain="0",
                                   tax_year: int = 2026,
                                   rules: Optional[DomainRules] = None) -> FiduciaryTax:
    """Income tax on an estate or non-grantor trust.

    The bracket schedule is compressed: the top rate arrives at $16,000 of
    taxable income rather than in the hundreds of thousands. That compression
    is the whole reason distributing income to beneficiaries usually costs less
    than retaining it, so it is reported explicitly.
    """
    pack = _rules(tax_year, rules)
    ordinary_schedule = [(Decimal(b["rate"]),
                          None if b["up_to"] is None else Decimal(b["up_to"]))
                         for b in pack.get("fiduciary_brackets")]
    gain_schedule = [(Decimal(b["rate"]), None if b["up_to"] is None else Decimal(b["up_to"]))
                     for b in pack.get("fiduciary_capital_gain_brackets")]

    ordinary = max(Decimal("0"), parse_decimal(ordinary_income, "ordinary_income"))
    gain = max(Decimal("0"), parse_decimal(long_term_gain, "long_term_gain"))

    ordinary_bands, ordinary_tax, _, _ = apply_bands(ordinary, ordinary_schedule)
    # Gains stack on ordinary income here exactly as they do for individuals.
    gain_bands, gain_tax, _, _ = apply_bands(gain, gain_schedule, floor=ordinary)

    top_at = ordinary_schedule[-2][1] if len(ordinary_schedule) > 1 else Decimal("0")
    notes = [
        f"Estates and trusts reach the top rate at ${top_at:,} of taxable income. "
        "Income distributed to a beneficiary is generally taxed on the beneficiary's "
        "return instead, often at a much lower rate.",
        "The distribution deduction, distributable net income and the separate share "
        "rule are not computed here; this is tax on the taxable income given.",
    ]
    if gain > 0:
        notes.append("Capital gains are usually allocated to corpus and so stay with the "
                     "trust even when income is distributed. Whether they do is a "
                     "question for the trust instrument and state law.")

    return FiduciaryTax(
        taxable_income=Money(ordinary + gain), ordinary_income=Money(ordinary),
        long_term_gain=Money(gain), ordinary_tax=Money(ordinary_tax),
        capital_gain_tax=Money(gain_tax), total_tax=Money(ordinary_tax + gain_tax),
        ordinary_bands=ordinary_bands, gain_bands=gain_bands,
        top_rate_reached_at=Money(top_at),
        rule_bundle_ref=pack.bundle_id, notes=notes)
