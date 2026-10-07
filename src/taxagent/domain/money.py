"""Monetary and quantity arithmetic.

Money is Decimal-only. Constructing Money from a float raises: binary floats
cannot represent cents exactly, and a silent 0.1 + 0.2 error in a tax basis is
indistinguishable from a real difference during reconciliation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Union

CENTS = Decimal("0.01")
QUANTITY_PRECISION = Decimal("0.000001")

Numeric = Union[str, int, Decimal]


class MoneyError(ValueError):
    """Invalid monetary value or operation."""


def parse_decimal(value: Numeric, field: str = "value") -> Decimal:
    if isinstance(value, float):
        raise MoneyError(
            f"{field}: float is not an accepted monetary/quantity input; "
            "pass a decimal string such as '1234.56'"
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (str, int)):
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise MoneyError(f"{field}: not a decimal: {value!r}") from exc
    raise MoneyError(f"{field}: unsupported type {type(value).__name__}")


def parse_quantity(value: Numeric, field: str = "quantity") -> Decimal:
    qty = parse_decimal(value, field)
    return qty.quantize(QUANTITY_PRECISION, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise MoneyError("Money.amount must be Decimal; use Money.of()")
        if not (isinstance(self.currency, str) and len(self.currency) == 3):
            raise MoneyError(f"invalid currency: {self.currency!r}")
        object.__setattr__(self, "currency", self.currency.upper())

    # --- construction ----------------------------------------------------
    @classmethod
    def of(cls, value: Numeric, currency: str = "USD") -> "Money":
        return cls(parse_decimal(value, "amount"), currency)

    @classmethod
    def zero(cls, currency: str = "USD") -> "Money":
        return cls(Decimal("0"), currency)

    # --- helpers ---------------------------------------------------------
    def _check(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise MoneyError("operand is not Money")
        if other.currency != self.currency:
            raise MoneyError(f"currency mismatch: {self.currency} vs {other.currency}")

    def quantized(self) -> "Money":
        return Money(self.amount.quantize(CENTS, rounding=ROUND_HALF_UP), self.currency)

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount >= other.amount

    def times(self, quantity: Numeric) -> "Money":
        return Money(self.amount * parse_decimal(quantity, "quantity"), self.currency)

    def per_unit(self, quantity: Numeric) -> Decimal:
        qty = parse_decimal(quantity, "quantity")
        if qty == 0:
            raise MoneyError("cannot divide money by zero quantity")
        return self.amount / qty

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def to_json(self) -> str:
        return format(self.quantized().amount, "f")

    def __str__(self) -> str:
        return f"{self.to_json()} {self.currency}"


def sum_money(values, currency: str = "USD") -> Money:
    total = Money.zero(currency)
    for value in values:
        total = total + value
    return total
