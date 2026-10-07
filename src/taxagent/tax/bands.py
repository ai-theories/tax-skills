"""Applying a graduated rate schedule, in one place.

Every graduated tax in this package works the same way: slice an amount across
bands, optionally sitting on top of a floor so a preferential rate lands where
the stacked income actually puts it. Federal ordinary tax, capital gains, AMT,
the fiduciary schedule and the estate and gift schedule all use it.

It lived inside the federal engine until the estate engine needed it too.
Copying it would have been the start of two implementations that drift, and
the stacking rule is exactly the part that was wrong in the code this project
replaced.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..domain.money import Money

Schedule = Sequence[Tuple[Decimal, Optional[Decimal]]]


@dataclass(frozen=True)
class Band:
    rate: Decimal
    income_in_band: Money
    tax: Money

    def to_json(self) -> Dict[str, Any]:
        return {"rate": f"{self.rate * 100:.0f}%",
                "income_in_band": self.income_in_band.to_json(),
                "tax": self.tax.to_json()}


def apply_bands(amount: Decimal, bands: Schedule,
                floor: Decimal = Decimal("0")) -> Tuple[List[Band], Decimal, Decimal,
                                                        Optional[Decimal]]:
    """Tax `amount` sitting on top of `floor`, returning per-band detail.

    Returns (bands, total tax, marginal rate, the ceiling of the band the last
    dollar fell in). `floor` is what makes stacking correct: a long-term gain
    on top of ordinary income is taxed where the stack lands, not from zero.
    """
    detail: List[Band] = []
    total = Decimal("0")
    lower = Decimal("0")
    remaining = amount
    marginal = bands[-1][0]
    ceiling: Optional[Decimal] = None

    for rate, upper in bands:
        band_top = upper if upper is not None else None
        start = max(lower, floor)
        end = band_top if band_top is not None else None
        width = (end - start) if end is not None else None
        if width is not None and width <= 0:
            lower = band_top
            continue
        take = remaining if width is None else min(remaining, width)
        if take > 0:
            total += take * rate
            detail.append(Band(rate, Money(take), Money(take * rate).quantized()))
            remaining -= take
            marginal = rate
            ceiling = band_top
        if remaining <= 0:
            break
        lower = band_top if band_top is not None else lower
    return detail, total, marginal, ceiling


def schedule_from(raw: Sequence[Dict[str, Any]]) -> List[Tuple[Decimal, Optional[Decimal]]]:
    """A pack's band list as a schedule."""
    return [(Decimal(b["rate"]), None if b["up_to"] is None else Decimal(b["up_to"]))
            for b in raw]
