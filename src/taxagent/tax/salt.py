"""The federal cap on deducting state and local tax, and state coverage.

Two different things live here on purpose. The cap is a federal rule with
published figures, so it is computed. A state's own income tax is not: no
state pack is bound in this deployment, and the registry below refuses rather
than letting a federal-only answer be read as a full picture.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ..decisions.rule_registry import DomainRules, RulePackError, domain_rules
from ..domain import codes
from ..domain.money import Money, parse_decimal

DOMAIN = "salt"
FILING_STATUSES = ("SINGLE", "MFJ", "MFS", "HOH")

# States whose own tax this deployment can compute. Empty, and that is the
# honest state of it: a state pack needs that state's brackets, its treatment
# of federal deductions, its capital gain treatment and its own source, and
# none of that has been transcribed or reviewed. Listing states here without
# those packs would be the single most damaging thing this module could do.
COVERED_STATES: Tuple[str, ...] = ()

STATE_PACK_REQUIREMENTS = (
    "the state's rate schedule for the tax year, by filing status",
    "whether the state begins from federal AGI, federal taxable income, or its own base",
    "the state's treatment of capital gains and of the federal deduction",
    "any state-level AMT, surtax or local add-on",
    "a citation to the state revenue authority's published figures",
)


def _rules(tax_year: int, rules: Optional[DomainRules]) -> DomainRules:
    return rules or domain_rules(DOMAIN, tax_year)


def _check_status(status: str) -> str:
    upper = str(status).upper()
    if upper not in FILING_STATUSES:
        raise RulePackError(
            codes.WRONG_RULE_YEAR,
            f"unknown filing status {status!r}; expected one of {', '.join(FILING_STATUSES)}")
    return upper


@dataclass(frozen=True)
class SaltCap:
    state_and_local_tax_paid: Money
    filing_status: str
    magi: Money
    statutory_cap: Money
    phase_down_threshold: Money
    magi_over_threshold: Money
    reduction: Money
    floor: Money
    effective_cap: Money
    deductible: Money
    disallowed: Money
    fully_phased_down_at: Money
    rule_bundle_ref: str
    notes: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "state_and_local_tax_paid":
                str(self.state_and_local_tax_paid.quantized().amount),
            "filing_status": self.filing_status,
            "magi": str(self.magi.quantized().amount),
            "statutory_cap": str(self.statutory_cap.quantized().amount),
            "phase_down_threshold": str(self.phase_down_threshold.quantized().amount),
            "magi_over_threshold": str(self.magi_over_threshold.quantized().amount),
            "reduction": str(self.reduction.quantized().amount),
            "floor": str(self.floor.quantized().amount),
            "effective_cap": str(self.effective_cap.quantized().amount),
            "deductible": str(self.deductible.quantized().amount),
            "disallowed": str(self.disallowed.quantized().amount),
            "fully_phased_down_at": str(self.fully_phased_down_at.quantized().amount),
            "rule_bundle_ref": self.rule_bundle_ref,
            "notes": list(self.notes),
        }


def calculate_salt_cap(state_and_local_tax_paid, magi, filing_status: str = "MFJ",
                       tax_year: int = 2026,
                       rules: Optional[DomainRules] = None) -> SaltCap:
    """How much state and local tax is deductible after the cap phases down.

    The cap does not switch off at the threshold; it falls by 30 cents per
    dollar of MAGI above it, and stops falling at the floor. Treating it as a
    cliff overstates the deduction just below the threshold and understates it
    well above.

    This only applies if the return itemizes at all. Whether itemizing beats
    the standard deduction is not decided here.
    """
    pack = _rules(tax_year, rules)
    status = _check_status(filing_status)
    cap_rules = pack.get("salt_cap")

    paid = max(Decimal("0"), parse_decimal(state_and_local_tax_paid,
                                           "state_and_local_tax_paid"))
    income = max(Decimal("0"), parse_decimal(magi, "magi"))

    cap = Decimal(pack.for_status(cap_rules["cap"], status))
    threshold = Decimal(pack.for_status(cap_rules["phase_down_threshold"], status))
    floor = Decimal(pack.for_status(cap_rules["floor"], status))
    rate = Decimal(cap_rules["phase_down_rate"])

    excess = max(Decimal("0"), income - threshold)
    reduction = min(excess * rate, cap - floor)
    effective = max(floor, cap - excess * rate)
    deductible = min(paid, effective)
    fully_at = threshold + (cap - floor) / rate

    notes: List[str] = []
    if excess > 0 and effective > floor:
        notes.append(
            f"The cap is phasing down: it falls {rate * 100:.0f} cents per dollar of MAGI "
            f"above ${threshold:,}, reaching the ${floor:,} floor at ${fully_at:,.0f}. "
            "Income recognised this year therefore costs more than its own marginal rate.")
    elif effective == floor and excess > 0:
        notes.append(f"Fully phased down. Above ${fully_at:,.0f} of MAGI the cap is the "
                     f"${floor:,} floor and further income does not reduce it again.")
    if paid > effective:
        notes.append("The excess is simply lost; it does not carry forward.")
    notes.append("Applies only if the return itemizes. Whether itemizing beats the "
                 "standard deduction is not decided here.")
    notes.append("Many states let a pass-through entity pay the tax and deduct it above "
                 "this cap. Whether that is available is a state question, and no state "
                 "pack is bound in this deployment.")

    return SaltCap(
        state_and_local_tax_paid=Money(paid), filing_status=status, magi=Money(income),
        statutory_cap=Money(cap), phase_down_threshold=Money(threshold),
        magi_over_threshold=Money(excess), reduction=Money(reduction), floor=Money(floor),
        effective_cap=Money(effective), deductible=Money(deductible),
        disallowed=Money(max(Decimal("0"), paid - deductible)),
        fully_phased_down_at=Money(fully_at),
        rule_bundle_ref=pack.bundle_id, notes=notes)


def state_coverage(state: Optional[str] = None) -> Dict[str, Any]:
    """What this deployment can say about a state's own tax: nothing, and why.

    Returned as data rather than raised, so an agent can report the boundary
    instead of treating it as an error.
    """
    requested = (state or "").upper()
    return {
        "covered_states": list(COVERED_STATES),
        "requested_state": requested or None,
        "covered": bool(requested) and requested in COVERED_STATES,
        "federal_salt_cap_available": True,
        "what_a_state_pack_requires": list(STATE_PACK_REQUIREMENTS),
        "note": (
            "No state pack is bound in this deployment, so no state's own income tax is "
            "computed. The federal cap on deducting state and local tax is computed and "
            "is a different thing: it limits a federal deduction, it does not tell you "
            "what the state charges. Residency, domicile, part-year allocation and "
            "reciprocity are not modelled at all."),
    }
