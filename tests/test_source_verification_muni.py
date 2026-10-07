"""Municipal bond rules, pinned to the sections they were read from.

Unlike the other packs, almost nothing here is a figure. These are rules, and
a rule transcribed backwards — a deduction allowed where none is, a discount
treated as capital where it is ordinary — is wrong in a way no arithmetic test
would catch. Each test quotes the statute it checks.

Sources, all read at law.cornell.edu on 2026-10-06:
  IRC 1278(a)(2)(C)  de minimis market discount
  IRC 1276(a)(1)     market discount is ordinary income
  IRC 171(a)(2)      no deduction for premium on a tax-exempt bond
  IRC 171(c)(1)      the election exists only for taxable bonds
  IRC 57(a)(5)       private activity interest is a preference item
  IRC 1411(c)(1)(A)(i) with IRC 103(a)  why exempt interest escapes the surtax
"""
from decimal import Decimal

import pytest

from taxagent.decisions.rule_registry import domain_rules

YEAR = 2026


@pytest.fixture(scope="module")
def muni():
    return domain_rules("muni", YEAR)


def test_the_de_minimis_fraction_matches_the_statute(muni):
    # 1278(a)(2)(C): "less than 1/4 of 1 percent of the stated redemption price
    # of the bond at maturity multiplied by the number of complete years to
    # maturity". A quarter of one percent is 0.0025, not 0.25.
    assert str(muni.get("de_minimis_fraction")) == "0.0025"
    assert Decimal(muni.get("de_minimis_fraction")) * 4 == Decimal("0.01")


def test_market_discount_above_the_threshold_is_ordinary(muni):
    # 1276(a)(1): gain on disposition is treated as ordinary income to the
    # extent of accrued market discount. Recording this as capital gain would
    # understate the tax by the difference between the two rate schedules.
    assert muni.get("market_discount_character") == "ordinary_income"
    assert muni.get("de_minimis_discount_character") == "capital_gain"


def test_no_deduction_is_allowed_for_premium_on_a_tax_exempt_bond(muni):
    # 171(a)(2): "In the case of any bond the interest on which is excludable
    # from gross income, no deduction shall be allowed for the amortizable bond
    # premium for the taxable year."
    assert muni.get("premium_deduction_allowed") == "none"


def test_premium_amortization_is_not_elective_for_a_tax_exempt_bond(muni):
    # 171(c)(1) offers the election only "In the case of bonds the interest on
    # which is not excludible from gross income", so there is no election here.
    assert muni.get("premium_amortization_election") == "mandatory_for_tax_exempt"
    assert muni.get("premium_basis_adjustment") == "reduce_basis_by_amortization"


def test_private_activity_interest_is_a_preference_item(muni):
    # 57(a)(5)(A): interest on specified private activity bonds is an item of
    # tax preference.
    assert muni.get("private_activity_amt_preference") == "preference_item"


def test_the_2009_and_2010_exception_is_exactly_two_years(muni):
    # 57(a)(5)(C)(vi) excepts bonds issued after 31 December 2008 and before
    # 1 January 2011. Widening this by a year would hand an exemption to bonds
    # that do not have one.
    years = [int(y) for y in muni.get("private_activity_amt_exempt_issue_years")]
    assert years == [2009, 2010]


def test_exempt_interest_is_recorded_as_outside_net_investment_income(muni):
    """And recorded as a derivation, because the statute does not say it.

    1411(c)(1)(A)(i) reaches "gross income from interest"; 103(a) keeps this
    interest out of gross income. The pack's authority names both sections
    rather than implying 1411 carves it out directly.
    """
    assert muni.get("tax_exempt_interest_in_net_investment_income") == "excluded"
    authority = muni.authorities["tax_exempt_interest_in_net_investment_income"]
    assert "1411" in authority and "103" in authority
    assert "read with" in authority


def test_exempt_interest_still_counts_toward_provisional_income(muni):
    # 86(b)(2)(B) adds it back. This is why "tax-free" overstates the position
    # for a retiree, and the engine says so rather than computing it.
    assert muni.get("tax_exempt_interest_in_provisional_income") == "included"


def test_the_pack_records_what_was_verified_and_what_was_not():
    import os
    import yaml
    from taxagent.application.bootstrap import PROJECT_ROOT

    path = os.path.join(PROJECT_ROOT, "rules", "tax", "parameter-packs",
                        "us-federal-muni-2026.v1.yaml")
    review = yaml.safe_load(open(path, encoding="utf-8"))["review"]
    assert "1278" in review["transcription_verified_against"]
    assert review["methodology_reviewed_by"] == "unassigned"
    # The derivation is called out rather than passed off as a quotation.
    assert "derivation" in review["note"]


def test_every_muni_rule_names_a_statute(muni):
    """A rule with a vague authority cannot be re-checked against anything."""
    for name, authority in muni.authorities.items():
        assert "IRC" in authority, f"{name} cites {authority!r}"
        assert any(ch.isdigit() for ch in authority), name
