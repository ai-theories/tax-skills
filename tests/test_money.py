from decimal import Decimal

import pytest

from taxagent.domain.money import Money, MoneyError, parse_quantity


def test_float_input_is_rejected():
    # 0.1 + 0.2 in binary floats is not 0.30; a basis that drifts silently is
    # indistinguishable from a real reconciliation difference.
    with pytest.raises(MoneyError):
        Money.of(1.1)


def test_decimal_string_arithmetic_is_exact():
    assert (Money.of("0.10") + Money.of("0.20")).to_json() == "0.30"
    assert (Money.of("10") - Money.of("3.33")).to_json() == "6.67"


def test_half_up_rounding_at_the_cent():
    assert Money.of("100.005").quantized().to_json() == "100.01"


def test_currency_mismatch_raises():
    with pytest.raises(MoneyError):
        Money.of("1.00", "USD") + Money.of("1.00", "EUR")


def test_quantity_precision_is_six_places():
    assert parse_quantity("1.23456789") == Decimal("1.234568")
