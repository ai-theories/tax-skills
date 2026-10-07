"""Every figure in the new domain packs, pinned to its published source.

Same discipline as test_source_verification: the figures below were read from
the source named in each test, not from the pack. Editing a pack without
re-reading the source fails here, which is the point.

Sources:
  Rev. Proc. 2025-32   https://www.irs.gov/pub/irs-drop/rp-25-32.pdf
  IRC 2001(c)          https://www.law.cornell.edu/uscode/text/26/2001
  SALT 2026            https://www.irs.gov/forms-pubs/correction-to-state-and-local-
                       income-tax-deduction-amount-in-the-2026-form-1040-es
"""
from decimal import Decimal

import pytest

from taxagent.decisions.rule_registry import domain_rules

YEAR = 2026


@pytest.fixture(scope="module")
def estate():
    return domain_rules("estate", YEAR)


@pytest.fixture(scope="module")
def international():
    return domain_rules("international", YEAR)


@pytest.fixture(scope="module")
def business():
    return domain_rules("business", YEAR)


@pytest.fixture(scope="module")
def compliance():
    return domain_rules("compliance", YEAR)


@pytest.fixture(scope="module")
def salt():
    return domain_rules("salt", YEAR)


# --- estate and gift ------------------------------------------------------

def test_basic_exclusion_matches_the_revenue_procedure(estate):
    # Rev. Proc. 2025-32: OBBBA increased the basic exclusion amount to
    # $15,000,000 for calendar year 2026.
    assert str(estate.get("basic_exclusion_amount")) == "15000000"


def test_annual_gift_exclusion_matches(estate):
    # Rev. Proc. 2025-32 sec. 4.42(1): the first $19,000 of gifts to any person.
    assert str(estate.get("annual_gift_exclusion")) == "19000"


def test_noncitizen_spouse_exclusion_matches(estate):
    # Rev. Proc. 2025-32 sec. 4.42(2): the first $194,000 of gifts to a spouse
    # who is not a citizen of the United States.
    assert str(estate.get("noncitizen_spouse_gift_exclusion")) == "194000"


def test_the_estate_rate_schedule_is_internally_consistent(estate):
    """Each cumulative figure must equal the previous plus that band's tax.

    Transcribing eleven brackets by hand invites a single-digit slip, and a
    slip in an early band shifts every figure after it. The statute publishes
    the cumulative amounts, so they can be rebuilt from the rates and checked.
    """
    published_cumulative = {
        "10000": "1800", "20000": "3800", "40000": "8200", "60000": "13000",
        "80000": "18200", "100000": "23800", "150000": "38800", "250000": "70800",
        "500000": "155800", "750000": "248300", "1000000": "345800",
    }
    schedule = estate.get("estate_gift_rate_schedule")
    running = Decimal("0")
    lower = Decimal("0")
    for band in schedule:
        if band["up_to"] is None:
            assert str(band["rate"]) == "0.40", "the top estate rate is 40 percent"
            break
        upper = Decimal(band["up_to"])
        running += (upper - lower) * Decimal(band["rate"])
        assert str(running.quantize(Decimal("1"))) == published_cumulative[str(upper)], (
            f"cumulative tax at {upper} does not match the statute")
        lower = upper


def test_fiduciary_brackets_match_table_five(estate):
    # Rev. Proc. 2025-32 Table 5: 10% to $3,300; $330 + 24% to $11,700;
    # $2,346 + 35% to $16,000; $3,851 + 37% above.
    published = [("0.10", "3300"), ("0.24", "11700"), ("0.35", "16000"),
                 ("0.37", None)]
    actual = [(str(b["rate"]), None if b["up_to"] is None else str(b["up_to"]))
              for b in estate.get("fiduciary_brackets")]
    assert actual == published


def test_the_fiduciary_cumulative_figures_match_the_table(estate):
    """The revenue procedure prints running totals; rebuild and compare."""
    published = {"3300": "330", "11700": "2346", "16000": "3851"}
    running, lower = Decimal("0"), Decimal("0")
    for band in estate.get("fiduciary_brackets"):
        if band["up_to"] is None:
            break
        upper = Decimal(band["up_to"])
        running += (upper - lower) * Decimal(band["rate"])
        assert str(running.quantize(Decimal("1"))) == published[str(upper)]
        lower = upper


def test_fiduciary_capital_gain_breakpoints_match(estate):
    # Rev. Proc. 2025-32 sec. 3.03, Estates and Trusts: zero rate to $3,300,
    # 15 percent rate to $16,250.
    bands = estate.get("fiduciary_capital_gain_brackets")
    assert str(bands[0]["up_to"]) == "3300"
    assert str(bands[1]["up_to"]) == "16250"
    assert str(bands[-1]["rate"]) == "0.20"


# --- international --------------------------------------------------------

def test_foreign_earned_income_exclusion_matches(international):
    # Rev. Proc. 2025-32 sec. 4.39: the exclusion under 911(b)(2)(D)(i) is
    # $132,900 for taxable years beginning in 2026.
    assert str(international.get("foreign_earned_income_exclusion")) == "132900"


def test_housing_fractions_are_the_statutory_ones(international):
    # IRC 911(c)(1)(B)(i): base is 16 percent of the exclusion.
    # IRC 911(c)(2)(A)(ii): the limit is 30 percent of it.
    assert str(international.get("housing_base_fraction")) == "0.16"
    assert str(international.get("housing_limit_fraction")) == "0.30"


def test_foreign_gift_reporting_threshold_matches(international):
    # Rev. Proc. 2025-32 sec. 4.47: reporting is required if the aggregate
    # value of gifts received in the taxable year exceeds $20,573.
    assert str(international.get("foreign_gift_reporting_threshold")) == "20573"


# --- business -------------------------------------------------------------

def test_section_179_figures_match(business):
    # Rev. Proc. 2025-32 sec. 3.24: the aggregate cost cannot exceed
    # $2,560,000; the SUV cost cannot exceed $32,000; the limitation is reduced
    # by cost placed in service exceeding $4,090,000.
    figures = business.get("section_179")
    assert str(figures["maximum"]) == "2560000"
    assert str(figures["phaseout_threshold"]) == "4090000"
    assert str(figures["sport_utility_vehicle_limit"]) == "32000"


def test_corporate_rate_is_the_statutory_flat_rate(business):
    assert str(business.get("corporate_rate")) == "0.21"


# --- compliance -----------------------------------------------------------

def test_failure_to_file_figures_match(compliance):
    # IRC 6651(a)(1): 5 percent a month, not exceeding 25 percent.
    # Rev. Proc. 2025-32 sec. 4.52: the minimum for a return more than 60 days
    # late is the lesser of $535 or 100 percent of the tax due.
    figures = compliance.get("failure_to_file")
    assert str(figures["monthly_rate"]) == "0.05"
    assert str(figures["maximum_rate"]) == "0.25"
    assert str(figures["minimum_after_60_days"]) == "535"


def test_failure_to_pay_rate_is_a_tenth_of_failure_to_file(compliance):
    """The ratio is the whole reason to file without paying.

    If these ever drift apart the advice built on them becomes wrong, so the
    relationship is asserted rather than just the two figures.
    """
    file_rate = Decimal(compliance.get("failure_to_file")["monthly_rate"])
    pay_rate = Decimal(compliance.get("failure_to_pay")["monthly_rate"])
    assert pay_rate * 10 == file_rate


def test_entity_late_filing_amounts_match(compliance):
    # Rev. Proc. 2025-32 sec. 4.55 and 4.56: $260 under both 6698(b)(1) and
    # 6699(b)(1) for returns required to be filed in 2027.
    assert str(compliance.get("partnership_late_filing")["per_partner_month"]) == "260"
    assert str(compliance.get("s_corporation_late_filing")
               ["per_shareholder_month"]) == "260"


def test_safe_harbour_percentages_are_statutory(compliance):
    # IRC 6654(d)(1): 90 percent of the current year, or 100 percent of the
    # prior year, rising to 110 percent where prior-year AGI exceeds $150,000
    # ($75,000 for a separate return).
    figures = compliance.get("estimated_tax_safe_harbour")
    assert str(figures["current_year_fraction"]) == "0.90"
    assert str(figures["prior_year_fraction"]) == "1.00"
    assert str(figures["prior_year_fraction_high_income"]) == "1.10"
    assert str(figures["high_income_agi_threshold"]) == "150000"
    assert str(figures["high_income_agi_threshold_mfs"]) == "75000"


# --- state and local ------------------------------------------------------

def test_salt_figures_match_the_irs_page(salt):
    # "For 2026, the limit is $40,400 ($20,200 if married filing separately)
    # and the overall limit is reduced if your modified adjusted gross income
    # is more than $505,000 ($252,500 if married filing separately) but will
    # not be reduced below $10,000 ($5,000 if married filing separately)."
    figures = salt.get("salt_cap")
    assert str(figures["cap"]["MFJ"]) == "40400"
    assert str(figures["cap"]["MFS"]) == "20200"
    assert str(figures["phase_down_threshold"]["MFJ"]) == "505000"
    assert str(figures["phase_down_threshold"]["MFS"]) == "252500"
    assert str(figures["floor"]["MFJ"]) == "10000"
    assert str(figures["floor"]["MFS"]) == "5000"


@pytest.mark.parametrize("status,complete", [("MFJ", "606333"), ("MFS", "303167")])
def test_the_salt_phase_down_rate_is_proved_not_remembered(salt, status, complete):
    """The IRS page does not state the rate; derive it from the endpoints.

    Congressional Research Service reporting gives the MAGI at which the cap
    reaches its floor. Only one rate carries the published cap down to the
    published floor exactly at that point, and it must hold for both filing
    statuses independently.
    """
    figures = salt.get("salt_cap")
    cap = Decimal(figures["cap"][status])
    threshold = Decimal(figures["phase_down_threshold"][status])
    floor = Decimal(figures["floor"][status])
    rate = Decimal(figures["phase_down_rate"])

    implied = threshold + (cap - floor) / rate
    assert str(implied.quantize(Decimal("1"))) == complete
    # ... and the rate implied by the endpoints is the one in the pack.
    derived = (cap - floor) / (Decimal(complete) - threshold)
    assert abs(derived - rate) < Decimal("0.001")


def test_no_state_is_claimed_as_covered():
    """The registry must stay empty until a state pack is actually reviewed.

    A state appearing here without its own pack would produce figures that
    look exactly like the verified ones, which is the worst failure available
    to this project.
    """
    from taxagent.tax.salt import COVERED_STATES, state_coverage

    assert COVERED_STATES == ()
    coverage = state_coverage("CA")
    assert coverage["covered"] is False
    assert coverage["federal_salt_cap_available"] is True
    assert len(coverage["what_a_state_pack_requires"]) >= 4


def test_each_pack_records_what_was_verified_and_what_was_not(
        estate, international, business, compliance, salt):
    """Transcription and methodology are separate claims, and stay separate."""
    import os
    import yaml
    from taxagent.application.bootstrap import PROJECT_ROOT

    packs = os.path.join(PROJECT_ROOT, "rules", "tax", "parameter-packs")
    for pack in (estate, international, business, compliance, salt):
        path = os.path.join(packs, f"{pack.bundle_id}.yaml")
        review = yaml.safe_load(open(path, encoding="utf-8"))["review"]
        assert review["transcription_verified_against"], pack.bundle_id
        assert review["methodology_reviewed_by"] == "unassigned", (
            f"{pack.bundle_id} claims a methodology review that has not happened")
