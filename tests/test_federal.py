"""Federal income tax, with worked examples computed by hand."""
from decimal import Decimal

import pytest

from taxagent.decisions.rule_registry import RulePackError, income_rules
from taxagent.tax import federal


def test_ordinary_tax_mfj_is_summed_band_by_band():
    # 10% x 24,800 = 2,480; 12% x 76,000 = 9,120; 22% x 52,000 = 11,440
    result = federal.calculate_ordinary_tax("152800", "MFJ")
    assert result.tax.to_json() == "23040.00"
    assert [b.tax.to_json() for b in result.bands] == ["2480.00", "9120.00", "11440.00"]
    assert result.marginal_rate == Decimal("0.22")


def test_income_below_the_first_threshold_is_all_at_ten_percent():
    result = federal.calculate_ordinary_tax("20000", "MFJ")
    assert result.tax.to_json() == "2000.00"
    assert result.marginal_rate == Decimal("0.10")
    assert result.headroom_in_bracket.to_json() == "4800.00"


def test_zero_income_is_zero_tax():
    assert federal.calculate_ordinary_tax("0", "SINGLE").tax.to_json() == "0.00"
    assert federal.calculate_ordinary_tax("-5000", "SINGLE").tax.to_json() == "0.00"


def test_gain_straddling_the_zero_band_is_split_not_rounded_up():
    # The bug this guards: taxing the whole gain at the rate its top dollar
    # reaches. Ordinary 90,000 leaves 8,900 of the MFJ 0% band; the remaining
    # 91,100 is taxed at 15% for 13,665 — not 15,000.
    result = federal.calculate_capital_gain_tax("100000", "90000", "MFJ")
    assert result.tax.to_json() == "13665.00"
    assert [(str(b.rate), b.income_in_band.to_json()) for b in result.bands] == [
        ("0.00", "8900.00"), ("0.15", "91100.00")]
    assert result.blended_rate == "13.66%"


def test_gain_entirely_inside_the_zero_band_is_untaxed():
    result = federal.calculate_capital_gain_tax("40000", "0", "MFJ")
    assert result.tax.to_json() == "0.00"
    assert result.blended_rate == "0.00%"


def test_gain_reaching_the_twenty_percent_band():
    result = federal.calculate_capital_gain_tax("100000", "600000", "MFJ")
    # 13,700 of room below 613,700 at 15%, then 86,300 at 20%.
    assert [(str(b.rate), b.income_in_band.to_json()) for b in result.bands] == [
        ("0.15", "13700.00"), ("0.20", "86300.00")]
    assert result.tax.to_json() == "19315.00"


def test_niit_is_the_lesser_of_nii_and_excess():
    over = federal.calculate_niit("45000", "260000", "MFJ")
    assert over.amount_subject.to_json() == "10000.00"     # excess binds
    assert over.tax.to_json() == "380.00"

    under = federal.calculate_niit("5000", "400000", "MFJ")
    assert under.amount_subject.to_json() == "5000.00"     # NII binds
    assert under.tax.to_json() == "190.00"


def test_niit_below_threshold_is_zero():
    assert federal.calculate_niit("50000", "240000", "MFJ").tax.to_json() == "0.00"


def test_mfs_niit_threshold_is_half():
    assert federal.calculate_niit("100000", "130000", "MFS").tax.to_json() == "190.00"


def test_capital_loss_is_limited_with_the_rest_carried_forward():
    result = federal.calculate_capital_loss_deduction("12000", "MFJ")
    assert result.deductible_this_year.to_json() == "3000.00"
    assert result.carryforward.to_json() == "9000.00"


def test_mfs_capital_loss_limit_is_halved():
    result = federal.calculate_capital_loss_deduction("12000", "MFS")
    assert result.deductible_this_year.to_json() == "1500.00"


def test_summary_combines_the_pieces_and_pins_its_rules():
    result = federal.summarize_federal("185000", "MFJ", long_term_gain="50000",
                                       net_investment_income="50000")
    assert result.ordinary.tax.to_json() == "23040.00"
    assert result.capital_gain.tax.to_json() == "7500.00"
    # MAGI 235,000 is under the 250,000 threshold, so no surtax.
    assert result.niit.tax.to_json() == "0.00"
    assert result.total_tax.to_json() == "30540.00"
    assert result.rule_bundle_ref == "us-federal-income-2026.v1"
    assert result.rule_bundle_hash.startswith("sha256:")


def test_itemized_deduction_overrides_the_standard():
    standard = federal.summarize_federal("185000", "MFJ")
    itemized = federal.summarize_federal("185000", "MFJ", deduction="60000")
    assert itemized.deduction.to_json() == "60000.00"
    assert itemized.total_tax < standard.total_tax


def test_unknown_filing_status_is_refused():
    with pytest.raises(RulePackError):
        federal.calculate_ordinary_tax("100000", "WIDOWER")


def test_uncovered_tax_year_is_refused_not_guessed():
    with pytest.raises(RulePackError) as excinfo:
        federal.summarize_federal("100000", "MFJ", tax_year=2019)
    assert "No reviewed income pack" in str(excinfo.value)


def test_every_status_has_a_full_parameter_set():
    rules = income_rules(2026)
    for status in federal.FILING_STATUSES:
        assert status in rules.ordinary_brackets
        assert status in rules.standard_deduction
        assert status in rules.capital_gain_brackets
        assert status in rules.niit_thresholds
        assert status in rules.capital_loss_limit


def test_brackets_are_monotonic_and_terminate():
    rules = income_rules(2026)
    for status, bands in rules.ordinary_brackets.items():
        rates = [r for r, _ in bands]
        assert rates == sorted(rates), f"{status} rates must rise"
        assert bands[-1][1] is None, f"{status} must have an open top band"
