"""Municipal bond tax treatment.

Three calculations, each answering a question an advisor is actually asked,
and each with a trap that makes the obvious answer wrong.

**What yield would a taxable bond need?** The comparison that decides whether
to buy a muni at all. The naive formula divides by one minus the federal rate
and stops. It understates for anyone paying state tax on the taxable
alternative, and understates again for anyone over the net investment income
tax threshold, because exempt interest never enters that base. It overstates
badly for a taxpayer in AMT holding a private activity bond, whose "tax-free"
interest is a preference item.

**Is this discount bond still tax-exempt?** Mostly not. Accretion of market
discount above a de minimis threshold is ordinary income on disposition, so a
muni bought below par can carry the highest rate in the code. The threshold is
a quarter of a percent per complete year to maturity, which is small enough
that most discount bonds fail it.

**What happens to premium?** It amortizes, mandatorily, with no deduction, and
it reduces basis. The holder who paid 105 and redeems at 100 has no loss to
claim: the five points were consumed by amortization they could not deduct.

What this module does not decide: whether a bond is a private activity bond,
and what any state charges. Both are supplied by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..decisions.rule_registry import DomainRules, RulePackError, domain_rules
from ..domain import codes
from ..domain.money import Money, parse_decimal

DOMAIN = "muni"
ONE = Decimal("1")


def _rules(tax_year: int, rules: Optional[DomainRules]) -> DomainRules:
    return rules or domain_rules(DOMAIN, tax_year)


def _rate(value, field: str) -> Decimal:
    rate = parse_decimal(value, field)
    if not Decimal("0") <= rate <= ONE:
        raise RulePackError(
            codes.VALIDATION_FAILED,
            f"{field} must be a fraction between 0 and 1; {rate} looks like a "
            "percentage. 37% is 0.37.")
    return rate


@dataclass(frozen=True)
class TaxableEquivalentYield:
    municipal_yield: str
    federal_rate: str
    state_rate: str
    niit_applies: bool
    state_exempt: bool
    amt_applies: bool
    issue_year: Optional[int]
    amt_preference_applies: bool
    effective_tax_rate_avoided: str
    taxable_equivalent_yield: str
    naive_federal_only: str
    uplift_from_state: str
    uplift_from_niit: str
    after_tax_municipal_yield: str
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "municipal_yield": self.municipal_yield,
            "federal_rate": self.federal_rate,
            "state_rate": self.state_rate,
            "niit_applies": self.niit_applies,
            "state_exempt": self.state_exempt,
            "amt_applies": self.amt_applies,
            "issue_year": self.issue_year,
            "amt_preference_applies": self.amt_preference_applies,
            "effective_tax_rate_avoided": self.effective_tax_rate_avoided,
            "taxable_equivalent_yield": self.taxable_equivalent_yield,
            "naive_federal_only": self.naive_federal_only,
            "uplift_from_state": self.uplift_from_state,
            "uplift_from_niit": self.uplift_from_niit,
            "after_tax_municipal_yield": self.after_tax_municipal_yield,
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_taxable_equivalent_yield(
        municipal_yield, federal_rate, state_rate="0", niit_applies: bool = False,
        state_exempt: bool = True, private_activity: bool = False,
        amt_applies: bool = False, issue_year: Optional[int] = None,
        tax_year: int = 2026,
        rules: Optional[DomainRules] = None) -> TaxableEquivalentYield:
    """What a taxable bond must yield to match this municipal one.

    `state_exempt` is whether the holder's own state exempts this bond, which
    is normally true only for a bond of their own state. An out-of-state bond
    is federally exempt and state taxable, so it avoids less and needs less
    from the taxable alternative.

    `amt_applies` together with `private_activity` is the case that reverses
    the answer: the interest is a preference item, so for that taxpayer the
    bond is not tax-exempt at all — unless the bond was issued in 2009 or 2010,
    which section 57(a)(5)(C)(vi) carves out. Those bonds are still in the
    market and a holder who assumes the preference applies to them is giving up
    yield for nothing.
    """
    pack = _rules(tax_year, rules)
    muni = _rate(municipal_yield, "municipal_yield")
    federal = _rate(federal_rate, "federal_rate")
    state = _rate(state_rate, "state_rate")

    niit_rate = Decimal("0.038") if niit_applies else Decimal("0")
    # Exempt interest never enters net investment income: section 1411 reaches
    # gross income from interest, and section 103 keeps this out of it. So the
    # surtax is part of what the muni avoids, not part of what it costs.
    avoided = federal + niit_rate + (state if state_exempt else Decimal("0"))

    notes: List[str] = []
    exempt_years = {int(y) for y in pack.get("private_activity_amt_exempt_issue_years")}
    issued_in_the_window = issue_year is not None and issue_year in exempt_years
    if private_activity and amt_applies and issued_in_the_window:
        notes.append(
            f"This is a private activity bond issued in {issue_year}, which section "
            "57(a)(5)(C)(vi) excepts from the preference. The interest stays exempt "
            "even in AMT, so the advantage survives where it would not for an "
            "otherwise identical bond issued a year either side.")
    elif private_activity and amt_applies:
        # The preference makes the interest taxable for this taxpayer, so there
        # is nothing to gross up. Reporting a tax-equivalent yield here would
        # be the single most misleading number this module could produce.
        avoided = Decimal("0")
        notes.append(
            "This is a private activity bond and the taxpayer is in AMT, so the "
            "interest is an item of tax preference under section 57(a)(5) and is "
            "not exempt for them. There is no yield advantage to gross up, and the "
            "equivalent yield below is simply the stated yield.")
    elif private_activity and issued_in_the_window:
        notes.append(
            f"This is a private activity bond issued in {issue_year}. Section "
            "57(a)(5)(C)(vi) excepts bonds issued in 2009 and 2010 from the "
            "preference, so AMT would not reach it even if the taxpayer fell into "
            "AMT later.")
    elif private_activity:
        notes.append(
            "This is a private activity bond. The interest is an AMT preference "
            "item, so the advantage disappears in any year the taxpayer falls into "
            "AMT. Whether they will is not decided here.")

    if avoided >= ONE:
        raise RulePackError(
            codes.VALIDATION_FAILED,
            f"the combined rate avoided is {avoided}, which is 100% or more; "
            "no taxable yield can match that")

    equivalent = muni / (ONE - avoided)
    naive = muni / (ONE - federal)
    state_only = muni / (ONE - (federal + (state if state_exempt else Decimal("0"))))

    if not state_exempt and state > 0:
        notes.append(
            "Out-of-state bond: federally exempt, taxable by the holder's state. "
            "An in-state bond of the same yield would be worth more to them.")
    if niit_applies:
        notes.append(
            "The 3.8% surtax is counted as avoided. Exempt interest is not net "
            "investment income, so a taxable bond of the same stated yield loses "
            "that much more.")
    notes.append(
        "Exempt interest is still added back when deciding how much Social "
        "Security is taxable, under section 86(b)(2)(B), so 'tax-free' overstates "
        "it for a retiree. That effect is not computed here.")
    notes.append(
        "Yields are compared before credit risk, call features, duration and "
        "liquidity, none of which this calculation sees.")

    def pct(value: Decimal) -> str:
        return f"{(value * Decimal('100')).quantize(Decimal('0.001'))}%"

    return TaxableEquivalentYield(
        municipal_yield=pct(muni), federal_rate=pct(federal), state_rate=pct(state),
        niit_applies=niit_applies, state_exempt=state_exempt, amt_applies=amt_applies,
        issue_year=issue_year,
        amt_preference_applies=bool(private_activity and not issued_in_the_window),
        effective_tax_rate_avoided=pct(avoided),
        taxable_equivalent_yield=pct(equivalent),
        naive_federal_only=pct(naive),
        uplift_from_state=pct(state_only - naive),
        uplift_from_niit=pct(equivalent - state_only),
        after_tax_municipal_yield=pct(muni),
        rule_bundle_ref=pack.bundle_id, notes=notes)


@dataclass(frozen=True)
class MarketDiscount:
    purchase_price: Money
    redemption_price: Money
    years_to_maturity: int
    market_discount: Money
    de_minimis_threshold: Money
    is_de_minimis: bool
    character: str
    ordinary_income_on_disposition: Money
    capital_gain_portion: Money
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "purchase_price": str(self.purchase_price.quantized().amount),
            "redemption_price": str(self.redemption_price.quantized().amount),
            "years_to_maturity": self.years_to_maturity,
            "market_discount": str(self.market_discount.quantized().amount),
            "de_minimis_threshold": str(self.de_minimis_threshold.quantized().amount),
            "is_de_minimis": self.is_de_minimis,
            "character": self.character,
            "ordinary_income_on_disposition":
                str(self.ordinary_income_on_disposition.quantized().amount),
            "capital_gain_portion": str(self.capital_gain_portion.quantized().amount),
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_market_discount(purchase_price, redemption_price,
                              years_to_maturity: int, tax_year: int = 2026,
                              rules: Optional[DomainRules] = None) -> MarketDiscount:
    """Whether a discount is de minimis, and what the accretion is taxed as.

    Below the threshold the discount is capital gain on sale or redemption.
    At or above it, the accrued portion is ordinary income — which is how a
    bond bought for its tax exemption ends up taxed at the top rate.

    `years_to_maturity` is complete years after acquisition, as the statute
    says. Rounding a partial year up raises the threshold and can turn a
    taxable discount into a de minimis one, so it is taken as given and never
    inferred.
    """
    pack = _rules(tax_year, rules)
    if years_to_maturity < 0:
        raise RulePackError(codes.VALIDATION_FAILED,
                            "years_to_maturity cannot be negative")

    purchase = parse_decimal(purchase_price, "purchase_price")
    redemption = parse_decimal(redemption_price, "redemption_price")
    if purchase <= 0 or redemption <= 0:
        raise RulePackError(codes.VALIDATION_FAILED,
                            "prices must be positive")

    discount = max(Decimal("0"), redemption - purchase)
    fraction = Decimal(pack.get("de_minimis_fraction"))
    threshold = redemption * fraction * Decimal(years_to_maturity)
    # "less than" the threshold: a discount exactly at it is not de minimis.
    de_minimis = discount < threshold

    notes: List[str] = []
    if discount == 0:
        character = "none"
        notes.append("Bought at or above par, so there is no market discount. "
                     "A premium has its own treatment and is not computed here.")
    elif de_minimis:
        character = str(pack.get("de_minimis_discount_character"))
        notes.append(
            f"The discount of {discount} is below the threshold of "
            f"{threshold.quantize(Decimal('0.01'))}, so it is treated as zero and "
            "the gain on sale or redemption is capital.")
    else:
        character = str(pack.get("market_discount_character"))
        notes.append(
            f"The discount of {discount} is at or above the threshold of "
            f"{threshold.quantize(Decimal('0.01'))}, so accrued market discount is "
            "ordinary income on disposition. This is the part people are surprised "
            "by: the interest stays exempt, the discount does not.")
        notes.append(
            "An election under section 1278(b) to accrue it currently changes the "
            "timing, not the character, and binds for all such bonds. Whether it "
            "has been made is not known here.")

    notes.append(
        f"The threshold is {fraction * 100:.2f}% of the redemption price for each "
        "complete year to maturity, so a long bond tolerates a much larger "
        "discount than a short one.")
    notes.append("Accrual is shown in full here. The portion that is ordinary "
                 "depends on how long the bond is actually held, which is not "
                 "known until it is sold.")

    ordinary = Decimal("0") if de_minimis else discount
    capital = discount if de_minimis else Decimal("0")
    return MarketDiscount(
        purchase_price=Money(purchase), redemption_price=Money(redemption),
        years_to_maturity=years_to_maturity, market_discount=Money(discount),
        de_minimis_threshold=Money(threshold), is_de_minimis=de_minimis,
        character=character, ordinary_income_on_disposition=Money(ordinary),
        capital_gain_portion=Money(capital),
        rule_bundle_ref=pack.bundle_id, notes=notes)


@dataclass(frozen=True)
class BondPremium:
    purchase_price: Money
    redemption_price: Money
    years_to_maturity: int
    premium: Money
    annual_amortization: Money
    deduction_allowed: Money
    adjusted_basis_at_maturity: Money
    loss_at_maturity: Money
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "purchase_price": str(self.purchase_price.quantized().amount),
            "redemption_price": str(self.redemption_price.quantized().amount),
            "years_to_maturity": self.years_to_maturity,
            "premium": str(self.premium.quantized().amount),
            "annual_amortization": str(self.annual_amortization.quantized().amount),
            "deduction_allowed": str(self.deduction_allowed.quantized().amount),
            "adjusted_basis_at_maturity":
                str(self.adjusted_basis_at_maturity.quantized().amount),
            "loss_at_maturity": str(self.loss_at_maturity.quantized().amount),
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_bond_premium(purchase_price, redemption_price, years_to_maturity: int,
                           tax_year: int = 2026,
                           rules: Optional[DomainRules] = None) -> BondPremium:
    """Mandatory amortization of premium on a tax-exempt bond, and its cost.

    Section 171(a)(2) allows no deduction, and section 171(c)(1) offers the
    election only for taxable bonds, so for a tax-exempt bond this is not a
    choice. The premium is consumed against basis and produces nothing: the
    holder who paid 105 and redeems at 100 has no loss.

    Amortization is shown straight-line for transparency. The constant-yield
    method the statute requires gives a different schedule year by year and the
    same total, so the figure that matters here — what is left at maturity — is
    unaffected.
    """
    pack = _rules(tax_year, rules)
    if years_to_maturity <= 0:
        raise RulePackError(codes.VALIDATION_FAILED,
                            "years_to_maturity must be at least 1 to amortize over")

    purchase = parse_decimal(purchase_price, "purchase_price")
    redemption = parse_decimal(redemption_price, "redemption_price")
    premium = max(Decimal("0"), purchase - redemption)

    notes: List[str] = []
    if premium == 0:
        notes.append("Bought at or below par, so there is no premium to amortize.")
    else:
        notes.append(
            "No deduction is allowed for premium on a tax-exempt bond, and the "
            "election that would make amortization optional exists only for "
            "taxable bonds. It is mandatory here and produces nothing deductible.")
        notes.append(
            "Basis falls by the amortization each year, so at maturity it equals "
            "the redemption price and there is no loss. A holder who expected to "
            "claim one has already had the benefit, as a reduction in the exempt "
            "interest they report rather than as a deduction.")
        notes.append(
            "Selling before maturity uses the reduced basis, so the gain is larger "
            "than the original cost suggests.")
    notes.append(
        "Straight-line is shown. The statute requires a constant-yield schedule, "
        "which differs year by year and reaches the same place at maturity.")

    annual = premium / Decimal(years_to_maturity) if premium else Decimal("0")
    return BondPremium(
        purchase_price=Money(purchase), redemption_price=Money(redemption),
        years_to_maturity=years_to_maturity, premium=Money(premium),
        annual_amortization=Money(annual),
        # The whole point: nothing is deductible.
        deduction_allowed=Money(Decimal("0")),
        adjusted_basis_at_maturity=Money(redemption),
        loss_at_maturity=Money(Decimal("0")),
        rule_bundle_ref=pack.bundle_id, notes=notes)
