"""Wash-sale behaviour, including the cases the blueprint requires."""
from datetime import date
from decimal import Decimal

import pytest

from taxagent.domain import codes
from taxagent.domain.coverage import CoverageInterval
from taxagent.domain.identities import Account, Registration
from taxagent.domain.money import Money
from taxagent.domain.tax_lots import Action, BasisSource, Transaction, make_lot
from taxagent.portfolio.snapshot_builder import build_snapshot
from taxagent.tax.lot_gain_loss import ProposedSale, calculate_gain_loss
from taxagent.tax.wash_sale_screen import ScreenStatus, screen_wash_sales

TENANT = "tenant_ria_1"
SALE_DATE = date(2026, 6, 15)
AS_OF = date(2026, 9, 30)          # forward window already closed


def _snapshot(transactions, coverage=None, accounts=None, as_of=AS_OF):
    accounts = accounts or [
        Account("acct_tax", TENANT, "tu_1", Registration.TAXABLE, "Schwab", "o"),
        Account("acct_roth", TENANT, "tu_1", Registration.ROTH_IRA, "Fidelity", "o"),
    ]
    lots = [make_lot("lot_loss", "acct_tax", "ARKK", "100", date(2025, 1, 2),
                     Money.of("12000.00"), BasisSource.CUSTODIAN_COVERED, True)]
    coverage = coverage or [
        CoverageInterval(a.account_id, date(2024, 1, 1), as_of, "src") for a in accounts
    ]
    return build_snapshot(TENANT, "acct_tax", as_of, accounts, lots, transactions, coverage, ["fx"])


def _sell(snapshot, quantity="100"):
    return calculate_gain_loss(snapshot, [
        ProposedSale("acct_tax", "ARKK", Decimal(quantity), Money.of("62.00"),
                     lot_id="lot_loss", sale_date=SALE_DATE)])


def test_no_replacement_leaves_loss_intact():
    snapshot = _snapshot([])
    screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions)
    assert screen.screens[0].matched_quantity == Decimal("0")
    assert screen.screens[0].disallowed_loss.to_json() == "0.00"
    assert screen.status is ScreenStatus.SCREENED_COMPLETE


def test_partial_quantity_allocates_only_matched_shares():
    # 40 replacement shares against a 100-share loss sale: 40% of the loss is
    # disallowed and the remaining 60 shares keep their loss.
    snapshot = _snapshot([Transaction("t1", "acct_tax", "ARKK", Action.BUY,
                                      date(2026, 6, 20), Decimal("40"), Money.of("2500.00"))])
    screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions)
    sale = screen.screens[0]
    assert sale.matched_quantity == Decimal("40")
    assert sale.unmatched_quantity == Decimal("60")
    assert sale.loss.to_json() == "5800.00"
    assert sale.disallowed_loss.to_json() == "2320.00"
    assert sale.allowed_loss.to_json() == "3480.00"


def test_replacement_in_roth_is_permanently_disallowed():
    # Rev. Rul. 2008-5: no basis adjustment is available in an IRA, so the loss
    # is gone rather than deferred.
    snapshot = _snapshot([Transaction("t1", "acct_roth", "ARKK", Action.BUY,
                                      date(2026, 6, 20), Decimal("100"), Money.of("6200.00"))])
    screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions)
    sale = screen.screens[0]
    assert sale.permanently_disallowed.to_json() == "5800.00"
    assert sale.matches[0].treatment == codes.PERMANENT_DISALLOWANCE
    assert any(f.code == codes.PERMANENT_DISALLOWANCE for f in screen.findings)


def test_purchase_outside_the_window_does_not_match():
    snapshot = _snapshot([Transaction("t1", "acct_tax", "ARKK", Action.BUY,
                                      date(2026, 7, 16), Decimal("100"), Money.of("6200.00"))])
    screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions)
    assert screen.screens[0].matched_quantity == Decimal("0")


def test_window_boundaries_are_inclusive():
    for trade_date in (SALE_DATE - __import__("datetime").timedelta(days=30),
                       SALE_DATE + __import__("datetime").timedelta(days=30)):
        snapshot = _snapshot([Transaction("t1", "acct_tax", "ARKK", Action.BUY,
                                          trade_date, Decimal("10"), Money.of("620.00"))])
        screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions)
        assert screen.screens[0].matched_quantity == Decimal("10")


def test_dividend_reinvestment_counts_as_a_replacement():
    snapshot = _snapshot([Transaction("t1", "acct_tax", "ARKK", Action.DIVIDEND_REINVEST,
                                      date(2026, 6, 18), Decimal("3"), Money.of("186.00"))])
    screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions)
    assert screen.screens[0].matched_quantity == Decimal("3")


def test_different_ticker_is_never_asserted_to_be_identical():
    snapshot = _snapshot([Transaction("t1", "acct_tax", "VTI", Action.BUY,
                                      date(2026, 6, 20), Decimal("100"), Money.of("26000.00"))])
    screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions)
    assert screen.screens[0].matched_quantity == Decimal("0")
    # ... and the screen says the question was not decided rather than implying "safe".
    assert any(f.code == codes.EQUIVALENCE_NOT_ASSESSED for f in screen.findings)


def test_coverage_gap_prevents_a_complete_screen():
    short_coverage = [
        CoverageInterval("acct_tax", date(2026, 6, 1), AS_OF, "src"),
        CoverageInterval("acct_roth", date(2026, 7, 1), AS_OF, "src"),   # starts after the window
    ]
    snapshot = _snapshot([], coverage=short_coverage)
    screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions)
    assert screen.status is ScreenStatus.SCREENED_WITH_GAPS
    assert any(f.code == codes.COVERAGE_GAP for f in screen.findings)


def test_unknown_related_account_prevents_household_clearance():
    snapshot = _snapshot([])
    screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions,
                               known_related_account_ids=["acct_spouse_ext"])
    assert screen.status is ScreenStatus.SCREENED_WITH_GAPS
    assert any(f.code == codes.UNKNOWN_RELATED_ACCOUNTS for f in screen.findings)


def test_open_forward_window_is_reported_and_blocks_completeness():
    # As-of is inside the 30 days after the sale, so a later purchase could
    # still create a wash sale that this screen cannot see.
    snapshot = _snapshot([], as_of=date(2026, 6, 20))
    screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions)
    assert screen.status is ScreenStatus.SCREENED_WITH_GAPS
    assert screen.forward_monitoring_until == date(2026, 7, 15)
    assert any(f.code == codes.FUTURE_WINDOW_OPEN for f in screen.findings)


def test_replacement_shares_are_not_reused_across_two_sales():
    accounts = [Account("acct_tax", TENANT, "tu_1", Registration.TAXABLE, "Schwab", "o")]
    lots = [
        make_lot("lot_a", "acct_tax", "ARKK", "50", date(2025, 1, 2), Money.of("6000.00"),
                 BasisSource.CUSTODIAN_COVERED, True),
        make_lot("lot_b", "acct_tax", "ARKK", "50", date(2025, 2, 2), Money.of("6000.00"),
                 BasisSource.CUSTODIAN_COVERED, True),
    ]
    coverage = [CoverageInterval("acct_tax", date(2024, 1, 1), AS_OF, "src")]
    transactions = [Transaction("t1", "acct_tax", "ARKK", Action.BUY, date(2026, 6, 20),
                                Decimal("50"), Money.of("3100.00"))]
    snapshot = build_snapshot(TENANT, "acct_tax", AS_OF, accounts, lots, transactions,
                              coverage, ["fx"])
    result = calculate_gain_loss(snapshot, [
        ProposedSale("acct_tax", "ARKK", Decimal("50"), Money.of("62.00"), lot_id="lot_a",
                     sale_date=SALE_DATE),
        ProposedSale("acct_tax", "ARKK", Decimal("50"), Money.of("62.00"), lot_id="lot_b",
                     sale_date=SALE_DATE),
    ])
    screen = screen_wash_sales(snapshot, result.dispositions)
    matched = [s.matched_quantity for s in screen.screens]
    assert matched == [Decimal("50"), Decimal("0")]


@pytest.mark.parametrize("registration,account_id", [
    (Registration.TRADITIONAL_IRA, "acct_tira"),
    (Registration.QUALIFIED_PLAN, "acct_plan"),
])
def test_every_retirement_registration_disallows_permanently(registration, account_id):
    """Rev. Rul. 2008-5 covers all three, and only the Roth was exercised.

    The rule pack lists three registrations and the code treats them alike, so
    the Roth test was taken as covering all of them. It did not: removing
    TRADITIONAL_IRA from the pack failed only the test that reads the pack, not
    any test of what the screen then does. An ordinary wash sale would still
    have blocked the harvest, so the conformance cases could not tell the
    difference either — the loss would simply have been deferred into the
    replacement's basis instead of destroyed, with nothing to catch it.
    """
    accounts = [
        Account("acct_tax", TENANT, "tu_1", Registration.TAXABLE, "Schwab", "o"),
        Account(account_id, TENANT, "tu_1", registration, "Fidelity", "o"),
    ]
    snapshot = _snapshot(
        [Transaction("t1", account_id, "ARKK", Action.BUY, date(2026, 6, 20),
                     Decimal("100"), Money.of("6200.00"))],
        accounts=accounts)
    screen = screen_wash_sales(snapshot, _sell(snapshot).dispositions)
    sale = screen.screens[0]
    assert sale.permanently_disallowed.to_json() == "5800.00"
    assert sale.matches[0].treatment == codes.PERMANENT_DISALLOWANCE
    assert any(f.code == codes.PERMANENT_DISALLOWANCE for f in screen.findings)


def test_a_taxable_replacement_defers_rather_than_destroys():
    """The contrast that gives the three tests above their meaning.

    In a taxable account the same purchase disallows the loss now and adds it
    to the replacement's basis, so it is recovered on a later sale. Nothing is
    permanently disallowed.
    """
    snapshot = _snapshot([Transaction("t1", "acct_tax", "ARKK", Action.BUY,
                                      date(2026, 6, 20), Decimal("100"),
                                      Money.of("6200.00"))])
    sale = screen_wash_sales(snapshot, _sell(snapshot).dispositions).screens[0]
    assert sale.disallowed_loss.to_json() == "5800.00"
    assert sale.permanently_disallowed.to_json() == "0.00"
