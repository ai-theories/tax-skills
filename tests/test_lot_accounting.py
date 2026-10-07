from datetime import date

import pytest

from taxagent.portfolio.lot_accounting import (Character, SelectionMethod, add_years,
                                               holding_period, select_lots, InsufficientQuantity)


def test_one_year_anniversary_is_short_term():
    # The holding period begins the day after acquisition, so a sale on the
    # anniversary is not "more than one year" (Rev. Rul. 66-7).
    acquired = date(2025, 1, 15)
    assert holding_period(acquired, date(2026, 1, 15)) is Character.SHORT_TERM
    assert holding_period(acquired, date(2026, 1, 16)) is Character.LONG_TERM


def test_leap_day_anniversary():
    assert add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)
    assert holding_period(date(2024, 2, 29), date(2025, 2, 28)) is Character.SHORT_TERM
    assert holding_period(date(2024, 2, 29), date(2025, 3, 1)) is Character.LONG_TERM


def test_unknown_acquisition_date_is_undetermined():
    assert holding_period(None, date(2026, 1, 1)) is Character.UNDETERMINED


def test_insufficient_quantity_raises(simple_snapshot):
    lots = simple_snapshot.lots_for("acct_tax", "VTI")
    with pytest.raises(InsufficientQuantity):
        select_lots(lots, __import__("decimal").Decimal("500"), SelectionMethod.FIFO)
