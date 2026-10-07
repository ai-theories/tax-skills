"""Holding periods and lot selection.

Holding period follows Rev. Rul. 66-7: the period begins the day after
acquisition, and long-term requires more than one year. A sale on the
one-year anniversary is therefore short-term.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Sequence, Tuple

from ..decisions.rule_registry import TaxRules, default_rules
from ..domain.tax_lots import TaxLot


class Character(str, Enum):
    SHORT_TERM = "SHORT_TERM"
    LONG_TERM = "LONG_TERM"
    UNDETERMINED = "UNDETERMINED"


class SelectionMethod(str, Enum):
    SPECIFIC = "SPECIFIC"
    FIFO = "FIFO"
    LIFO = "LIFO"
    HIFO = "HIFO"
    MAX_LOSS = "MAX_LOSS"


class InsufficientQuantity(ValueError):
    pass


def add_years(anchor: date, years: int) -> date:
    """Anniversary date, mapping 29 February onto 28 February in common years."""
    try:
        return anchor.replace(year=anchor.year + years)
    except ValueError:
        return anchor.replace(year=anchor.year + years, month=2, day=28)


def holding_period(acquisition_date: Optional[date], disposal_date: date,
                   rules: Optional[TaxRules] = None) -> Character:
    """Character of a disposal.

    The one-year threshold comes from the reviewed rule pack, never from a
    literal here: a tax rule in source has no version, no effective date and
    no authority.
    """
    if acquisition_date is None:
        return Character.UNDETERMINED
    if disposal_date <= acquisition_date:
        return Character.UNDETERMINED
    rules = rules or default_rules(disposal_date.year)
    threshold = add_years(acquisition_date, rules.long_term_holding_period_years)
    return Character.LONG_TERM if disposal_date > threshold else Character.SHORT_TERM


def holding_days(acquisition_date: Optional[date], disposal_date: date) -> Optional[int]:
    if acquisition_date is None:
        return None
    return (disposal_date - acquisition_date).days


def select_lots(
    lots: Sequence[TaxLot],
    quantity: Decimal,
    method: SelectionMethod = SelectionMethod.FIFO,
    price_per_share: Optional[Decimal] = None,
) -> List[Tuple[TaxLot, Decimal]]:
    """Return [(lot, quantity)] covering `quantity`.

    Ranking lots is a selection strategy only. Whether the intended basis
    treatment is achieved depends on the custodian honouring a specific
    identification instruction before settlement, which this function cannot
    establish; callers surface that as a separate step.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    ordered = list(lots)
    if method is SelectionMethod.FIFO:
        ordered.sort(key=lambda l: (l.acquisition_date or date.max, l.lot_id))
    elif method is SelectionMethod.LIFO:
        ordered.sort(key=lambda l: (l.acquisition_date or date.min, l.lot_id), reverse=True)
    elif method is SelectionMethod.HIFO:
        ordered.sort(key=lambda l: (-(l.basis_per_share() or Decimal("-1e18")), l.lot_id))
    elif method is SelectionMethod.MAX_LOSS:
        if price_per_share is None:
            raise ValueError("MAX_LOSS selection requires price_per_share")
        ordered.sort(key=lambda l: ((price_per_share - (l.basis_per_share() or price_per_share)), l.lot_id))

    picked: List[Tuple[TaxLot, Decimal]] = []
    remaining = quantity
    for lot in ordered:
        if remaining <= 0:
            break
        take = min(lot.quantity, remaining)
        if take > 0:
            picked.append((lot, take))
            remaining -= take
    if remaining > 0:
        raise InsufficientQuantity(
            f"requested {quantity} but only {quantity - remaining} available"
        )
    return picked
