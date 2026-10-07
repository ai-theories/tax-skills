"""The municipal bond engine, against the decisions it is used for.

Each test is a question an advisor is asked, with the obvious wrong answer
named, because the value of this engine is almost entirely in not giving it.
"""
from decimal import Decimal

import pytest

from taxagent.decisions.rule_registry import RulePackError
from taxagent.tax import municipal


def _pct(value: str) -> Decimal:
    return Decimal(value.rstrip("%"))


# --- taxable-equivalent yield ---------------------------------------------

def test_the_naive_formula_understates_for_a_high_earner():
    """The headline case, and the reason the engine exists.

    A 3.5% in-state bond for a 37% federal, 9.3% state, surtax-paying holder
    needs a 7.014% taxable bond to match it. Dividing by one minus the federal
    rate alone says 5.556%, which would reject taxable alternatives that are
    actually worse and accept munis that are actually better.
    """
    result = municipal.calculate_taxable_equivalent_yield(
        "0.035", "0.37", state_rate="0.093", niit_applies=True)
    assert result.taxable_equivalent_yield == "7.014%"
    assert result.naive_federal_only == "5.556%"
    assert _pct(result.taxable_equivalent_yield) - _pct(result.naive_federal_only) \
        > Decimal("1.4")


def test_an_out_of_state_bond_is_a_weaker_proposition():
    """Federally exempt, state taxable. People compare them as if identical."""
    in_state = municipal.calculate_taxable_equivalent_yield(
        "0.035", "0.37", state_rate="0.093", niit_applies=True)
    out_of_state = municipal.calculate_taxable_equivalent_yield(
        "0.035", "0.37", state_rate="0.093", niit_applies=True, state_exempt=False)
    assert (_pct(out_of_state.taxable_equivalent_yield)
            < _pct(in_state.taxable_equivalent_yield))
    assert out_of_state.taxable_equivalent_yield == "5.912%"


def test_the_surtax_is_counted_as_avoided():
    """Exempt interest never enters net investment income, so it is avoided.

    Treating the surtax as irrelevant to a muni comparison understates the
    muni by roughly half a point of yield at these rates.
    """
    with_surtax = municipal.calculate_taxable_equivalent_yield(
        "0.035", "0.37", state_rate="0.093", niit_applies=True)
    without = municipal.calculate_taxable_equivalent_yield(
        "0.035", "0.37", state_rate="0.093", niit_applies=False)
    assert _pct(with_surtax.taxable_equivalent_yield) > _pct(without.taxable_equivalent_yield)
    assert with_surtax.uplift_from_niit != "0.000%"


def test_a_private_activity_bond_in_amt_has_no_advantage_to_gross_up():
    """The case that reverses the answer rather than shading it.

    Section 57(a)(5) makes the interest a preference item, so for this taxpayer
    the bond is not exempt. Reporting a grossed-up yield here would be the most
    misleading number this module could produce.
    """
    result = municipal.calculate_taxable_equivalent_yield(
        "0.035", "0.37", state_rate="0.093", niit_applies=True,
        private_activity=True, amt_applies=True)
    assert result.taxable_equivalent_yield == "3.500%"
    assert result.effective_tax_rate_avoided == "0.000%"
    assert result.amt_preference_applies is True
    assert any("preference" in note for note in result.notes)


def test_a_private_activity_bond_outside_amt_is_warned_about_not_discounted():
    result = municipal.calculate_taxable_equivalent_yield(
        "0.035", "0.37", state_rate="0.093", niit_applies=True, private_activity=True)
    assert result.taxable_equivalent_yield == "7.014%"
    assert any("AMT" in note for note in result.notes)


@pytest.mark.parametrize("year,preference", [(2008, True), (2009, False),
                                             (2010, False), (2011, True)])
def test_the_2009_and_2010_window_decides_the_preference(year, preference):
    """A narrow carve-out with a total effect, and bonds from it still trade."""
    result = municipal.calculate_taxable_equivalent_yield(
        "0.035", "0.37", state_rate="0.093", niit_applies=True,
        private_activity=True, amt_applies=True, issue_year=year)
    assert result.amt_preference_applies is preference
    expected = "3.500%" if preference else "7.014%"
    assert result.taxable_equivalent_yield == expected


def test_a_rate_given_as_a_percentage_is_refused():
    """37 is not 0.37, and the difference is not recoverable from the output."""
    with pytest.raises(RulePackError):
        municipal.calculate_taxable_equivalent_yield("0.035", "37")


def test_a_combined_rate_of_one_hundred_percent_is_refused():
    with pytest.raises(RulePackError) as excinfo:
        municipal.calculate_taxable_equivalent_yield(
            "0.035", "0.60", state_rate="0.40", niit_applies=True)
    assert "no taxable yield can match" in str(excinfo.value)


def test_the_social_security_interaction_is_always_stated():
    """It is why "tax-free" overstates the position, and it is not computed."""
    result = municipal.calculate_taxable_equivalent_yield("0.035", "0.37")
    assert any("Social Security" in note for note in result.notes)


# --- market discount -------------------------------------------------------

def test_a_discount_above_the_threshold_is_ordinary_income():
    """The trap: a bond bought for its exemption carrying the top rate."""
    result = municipal.calculate_market_discount("950", "1000", 10)
    assert result.de_minimis_threshold.quantized().amount == Decimal("25.00")
    assert result.is_de_minimis is False
    assert result.character == "ordinary_income"
    assert result.ordinary_income_on_disposition.quantized().amount == Decimal("50.00")


def test_a_discount_below_the_threshold_stays_capital():
    result = municipal.calculate_market_discount("980", "1000", 10)
    assert result.is_de_minimis is True
    assert result.character == "capital_gain"
    assert result.capital_gain_portion.quantized().amount == Decimal("20.00")


def test_the_threshold_scales_with_maturity_not_price():
    """Two bonds, same discount, opposite answers.

    Twenty points of discount is de minimis over ten years and ordinary income
    over two. Advisors reason about the discount and forget the multiplier.
    """
    long_bond = municipal.calculate_market_discount("980", "1000", 10)
    short_bond = municipal.calculate_market_discount("980", "1000", 2)
    assert long_bond.is_de_minimis is True
    assert short_bond.is_de_minimis is False
    assert short_bond.de_minimis_threshold.quantized().amount == Decimal("5.00")


def test_a_discount_exactly_at_the_threshold_is_not_de_minimis():
    """The statute says "less than", and the boundary is where advice changes."""
    result = municipal.calculate_market_discount("975", "1000", 10)
    assert result.market_discount.quantized() == result.de_minimis_threshold.quantized()
    assert result.is_de_minimis is False


def test_a_bond_bought_at_par_has_no_discount():
    result = municipal.calculate_market_discount("1000", "1000", 10)
    assert result.market_discount.quantized().amount == Decimal("0.00")
    assert result.character == "none"


def test_zero_years_to_maturity_leaves_no_de_minimis_room():
    """The threshold is zero, so any discount at all is ordinary income."""
    result = municipal.calculate_market_discount("999", "1000", 0)
    assert result.de_minimis_threshold.quantized().amount == Decimal("0.00")
    assert result.is_de_minimis is False


def test_negative_years_are_refused():
    with pytest.raises(RulePackError):
        municipal.calculate_market_discount("950", "1000", -1)


# --- premium ---------------------------------------------------------------

def test_premium_on_a_tax_exempt_bond_buys_no_deduction():
    """Mandatory amortization, nothing deductible, and no loss at the end."""
    result = municipal.calculate_bond_premium("1050", "1000", 10)
    assert result.premium.quantized().amount == Decimal("50.00")
    assert result.annual_amortization.quantized().amount == Decimal("5.00")
    assert result.deduction_allowed.quantized().amount == Decimal("0.00")


def test_there_is_no_loss_at_maturity_however_large_the_premium():
    """The expectation clients arrive with, and it is wrong at any size."""
    for price in ("1010", "1050", "1200"):
        result = municipal.calculate_bond_premium(price, "1000", 10)
        assert result.loss_at_maturity.quantized().amount == Decimal("0.00")
        assert result.adjusted_basis_at_maturity.quantized().amount == Decimal("1000.00")


def test_the_mandatory_nature_is_stated_not_implied():
    result = municipal.calculate_bond_premium("1050", "1000", 10)
    joined = " ".join(result.notes)
    assert "no deduction" in joined.lower()
    assert "mandatory" in joined.lower()


def test_a_bond_bought_at_a_discount_has_no_premium():
    result = municipal.calculate_bond_premium("950", "1000", 10)
    assert result.premium.quantized().amount == Decimal("0.00")


def test_zero_years_is_refused_for_premium():
    """There is nothing to amortize over, and dividing by it would raise."""
    with pytest.raises(RulePackError):
        municipal.calculate_bond_premium("1050", "1000", 0)


def test_every_result_pins_the_rule_pack_it_used():
    for result in (
        municipal.calculate_taxable_equivalent_yield("0.035", "0.37"),
        municipal.calculate_market_discount("950", "1000", 10),
        municipal.calculate_bond_premium("1050", "1000", 10),
    ):
        assert result.rule_bundle_ref == "us-federal-muni-2026.v1"


# --- the calculator surface ------------------------------------------------

def test_an_omitted_boolean_takes_the_default_the_form_shows():
    """Found by running the server, not by any unit test.

    `flag()` hardcoded "no" for an absent field, so omitting `state_exempt` —
    whose declared default is "yes" — silently produced the out-of-state
    answer. A caller who did not know the field existed got a different and
    worse number with nothing to signal it. The failure is silent by
    construction: both answers are well-formed yields.
    """
    from taxagent.tax import calculators

    spec = {f["name"]: f.get("default") for f in calculators.spec("muni_yield")["fields"]}
    assert spec["state_exempt"] == "yes", "this test assumes the declared default"

    omitted = calculators.run("muni_yield", {
        "municipal_yield": "0.035", "federal_rate": "0.37",
        "state_rate": "0.093", "niit_applies": "yes"})
    explicit = calculators.run("muni_yield", {
        "municipal_yield": "0.035", "federal_rate": "0.37",
        "state_rate": "0.093", "niit_applies": "yes", "state_exempt": "yes"})
    assert omitted["taxable_equivalent_yield"] == explicit["taxable_equivalent_yield"]
    assert omitted["taxable_equivalent_yield"] == "7.014%"


def test_an_explicit_no_still_means_no():
    from taxagent.tax import calculators

    result = calculators.run("muni_yield", {
        "municipal_yield": "0.035", "federal_rate": "0.37",
        "state_rate": "0.093", "niit_applies": "yes", "state_exempt": "no"})
    assert result["taxable_equivalent_yield"] == "5.912%"


def test_every_boolean_field_in_every_calculator_honours_its_default():
    """The general form of the bug, across all nine domains.

    Any select field whose default is "yes" is exposed to it: omitting the
    field must give the same answer as passing the default explicitly.
    """
    from taxagent.tax import calculators

    probes = {
        "muni_yield": {"municipal_yield": "0.035", "federal_rate": "0.37",
                       "state_rate": "0.093"},
        "qbi": {"qbi": "300000", "taxable_income": "450000", "wages": "80000"},
        "filing_penalty": {"unpaid_tax": "10000", "days_late": "95"},
        "gift_exclusion": {"gift_amount": "50000"},
        "foreign_gift": {"amount_received": "150000"},
        "entity_penalty": {"entity_type": "partnership", "owners": "8",
                           "months_late": "4"},
    }
    for calc_id, base in probes.items():
        fields = calculators.spec(calc_id)["fields"]
        booleans = {f["name"]: f.get("default") for f in fields
                    if f.get("options") == ["no", "yes"] or f.get("options") == ["yes", "no"]}
        if not booleans:
            continue
        omitted = calculators.run(calc_id, dict(base))
        explicit = calculators.run(calc_id, {**base, **booleans})
        assert omitted == explicit, (
            f"{calc_id}: omitting {sorted(booleans)} changed the answer")
