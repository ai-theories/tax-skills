"""Figures pinned to Rev. Proc. 2025-32, read from the published document.

Transcribed on 2026-10-05 from https://www.irs.gov/pub/irs-drop/rp-25-32.pdf.
Editing a pack without updating this file fails the build, which forces the
figure to be re-read from the source rather than adjusted to fit.

This verifies transcription only. Whether the calculations built on these
figures treat every fact pattern correctly is a separate question.
"""
from decimal import Decimal

import pytest

from taxagent.decisions.rule_registry import income_rules

#: Section 4.01, Tables 1-4. Bracket ceilings in ascending order; the top band
#: is open-ended and carries no ceiling.
PUBLISHED_BRACKETS = {
    "MFJ":    ["24800", "100800", "211400", "403550", "512450", "768700"],
    "HOH":    ["17700", "67450", "105700", "201750", "256200", "640600"],
    "SINGLE": ["12400", "50400", "105700", "201775", "256225", "640600"],
    "MFS":    ["12400", "50400", "105700", "201775", "256225", "384350"],
}
PUBLISHED_STANDARD_DEDUCTION = {            # section 4.14(1)
    "MFJ": "32200", "HOH": "24150", "SINGLE": "16100", "MFS": "16100"}
PUBLISHED_CAPITAL_GAIN = {                  # section 4.03
    "MFJ": ("98900", "613700"), "MFS": ("49450", "306850"),
    "HOH": ("66200", "579600"), "SINGLE": ("49450", "545500")}
PUBLISHED_AMT_EXEMPTION = {                 # section 4.10
    "MFJ": "140200", "SINGLE": "90100", "MFS": "70100", "HOH": "90100"}
PUBLISHED_AMT_PHASEOUT_START = {
    "MFJ": "1000000", "SINGLE": "500000", "MFS": "500000", "HOH": "500000"}
PUBLISHED_AMT_COMPLETE_PHASEOUT = {         # used to confirm the 50% rate
    "MFJ": "1280400", "SINGLE": "680200", "MFS": "640200"}
PUBLISHED_AMT_BREAKPOINT = {
    "MFJ": "244500", "SINGLE": "244500", "HOH": "244500", "MFS": "122250"}
PUBLISHED_QBI_THRESHOLD = {                 # section 4.26
    "MFJ": "403500", "MFS": "201775", "SINGLE": "201750", "HOH": "201750"}
PUBLISHED_QBI_RANGE_TOP = {
    "MFJ": "553500", "MFS": "276775", "SINGLE": "276750", "HOH": "276750"}


@pytest.fixture(scope="module")
def rules():
    return income_rules(2026)


@pytest.mark.parametrize("status", sorted(PUBLISHED_BRACKETS))
def test_ordinary_brackets_match_the_revenue_procedure(rules, status):
    ceilings = [band[1] for band in rules.ordinary_brackets[status]]
    assert ceilings[-1] is None, f"{status} top band must be open-ended"
    assert [str(c) for c in ceilings[:-1]] == PUBLISHED_BRACKETS[status]


@pytest.mark.parametrize("status", sorted(PUBLISHED_STANDARD_DEDUCTION))
def test_standard_deduction_matches(rules, status):
    assert str(rules.standard_deduction[status]) == PUBLISHED_STANDARD_DEDUCTION[status]


@pytest.mark.parametrize("status", sorted(PUBLISHED_CAPITAL_GAIN))
def test_capital_gain_breakpoints_match(rules, status):
    zero_top, fifteen_top = PUBLISHED_CAPITAL_GAIN[status]
    bands = rules.capital_gain_brackets[status]
    assert str(bands[0][1]) == zero_top and bands[0][0] == Decimal("0.00")
    assert str(bands[1][1]) == fifteen_top and bands[1][0] == Decimal("0.15")
    assert bands[2][1] is None and bands[2][0] == Decimal("0.20")


@pytest.mark.parametrize("status", sorted(PUBLISHED_AMT_EXEMPTION))
def test_amt_exemption_and_thresholds_match(rules, status):
    assert str(rules.amt["exemption"][status]) == PUBLISHED_AMT_EXEMPTION[status]
    assert str(rules.amt["phaseout_threshold"][status]) == PUBLISHED_AMT_PHASEOUT_START[status]
    assert str(rules.amt["rate_breakpoint"][status]) == PUBLISHED_AMT_BREAKPOINT[status]


@pytest.mark.parametrize("status", sorted(PUBLISHED_AMT_COMPLETE_PHASEOUT))
def test_the_fifty_percent_phaseout_rate_is_implied_by_the_published_amounts(rules, status):
    # The revenue procedure gives no rate, but it publishes where the exemption
    # reaches zero. threshold + exemption / rate must equal that figure, which
    # holds only at 50%.
    exemption = rules.amt["exemption"][status]
    threshold = rules.amt["phaseout_threshold"][status]
    implied = threshold + exemption / rules.amt["phaseout_rate"]
    assert str(implied.quantize(Decimal("1"))) == PUBLISHED_AMT_COMPLETE_PHASEOUT[status]


@pytest.mark.parametrize("status", sorted(PUBLISHED_QBI_THRESHOLD))
def test_qbi_threshold_and_phase_in_width_match(rules, status):
    assert str(rules.qbi["threshold"][status]) == PUBLISHED_QBI_THRESHOLD[status]
    # The procedure publishes the top of the range; the pack stores the width.
    top = Decimal(PUBLISHED_QBI_RANGE_TOP[status])
    assert rules.qbi["threshold"][status] + rules.qbi["phase_in_range"][status] == top


def test_qbi_minimum_deduction_matches_the_obbba_amendment(rules):
    # Section 3.12: a $400 minimum with a $1,000 QBI floor, from 2026.
    assert str(rules.qbi["minimum_deduction"]) == "400"
    assert str(rules.qbi["minimum_deduction_qbi_floor"]) == "1000"


def test_statutory_figures_not_in_the_revenue_procedure(rules):
    # NIIT thresholds and the capital loss limit are fixed in statute and are
    # not inflation adjusted, so they never appear in an annual procedure.
    assert str(rules.niit_rate) == "0.038"                       # IRC 1411(a)
    assert str(rules.niit_thresholds["MFJ"]) == "250000"
    assert str(rules.niit_thresholds["SINGLE"]) == "200000"
    assert str(rules.niit_thresholds["MFS"]) == "125000"
    assert str(rules.capital_loss_limit["MFJ"]) == "3000"        # IRC 1211(b)
    assert str(rules.capital_loss_limit["MFS"]) == "1500"


def test_the_pack_records_what_was_verified_and_what_was_not():
    import os
    import yaml
    from taxagent.application.bootstrap import PROJECT_ROOT
    pack = yaml.safe_load(open(os.path.join(
        PROJECT_ROOT, "rules", "tax", "parameter-packs",
        "us-federal-income-2026.v1.yaml"), encoding="utf-8"))
    review = pack["review"]
    assert "rp-25-32" in review["transcription_verified_against"]
    # Methodology review is a different claim and is still outstanding.
    assert review["methodology_reviewed_by"] == "unassigned"
