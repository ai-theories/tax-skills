"""Lot-level gain and loss.

Every disposition is either calculated or explicitly blocked. A lot with
unknown basis produces a blocked entry with a reason code and is excluded from
totals; it never contributes a zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import CALCULATION_VERSIONS
from ..decisions.rule_registry import TaxRules, default_rules
from ..domain import codes
from ..domain.money import Money, parse_quantity, sum_money
from ..domain.snapshots import Snapshot
from ..domain.tax_lots import TaxLot
from ..portfolio.lot_accounting import (Character, SelectionMethod, holding_days,
                                        holding_period, select_lots)


@dataclass(frozen=True)
class ProposedSale:
    account_id: str
    security_id: str
    quantity: Decimal
    price_per_share: Money
    lot_id: Optional[str] = None
    sale_date: Optional[date] = None


@dataclass(frozen=True)
class LotDisposition:
    lot_id: str
    account_id: str
    security_id: str
    quantity: Decimal
    sale_date: date
    proceeds: Optional[Money]
    basis: Optional[Money]
    gain: Optional[Money]
    character: Character
    holding_days: Optional[int]
    blocked: bool = False
    block_code: Optional[str] = None

    @property
    def is_loss(self) -> bool:
        return self.gain is not None and self.gain.is_negative

    def to_json(self) -> Dict[str, Any]:
        return {
            "lot_id": self.lot_id,
            "account_id": self.account_id,
            "security_id": self.security_id,
            "quantity": format(self.quantity, "f"),
            "sale_date": self.sale_date.isoformat(),
            "proceeds": self.proceeds.to_json() if self.proceeds else None,
            "basis": self.basis.to_json() if self.basis else None,
            "gain": self.gain.to_json() if self.gain else None,
            "character": self.character.value,
            "holding_days": self.holding_days,
            "blocked": self.blocked,
            "block_code": self.block_code,
        }


@dataclass(frozen=True)
class GainLossResult:
    dispositions: Tuple[LotDisposition, ...]
    currency: str = "USD"
    calculation_version: str = CALCULATION_VERSIONS["lot_gain_loss"]
    rule_bundle_ref: str = ""

    @property
    def calculated(self) -> Tuple[LotDisposition, ...]:
        return tuple(d for d in self.dispositions if not d.blocked)

    @property
    def blocked(self) -> Tuple[LotDisposition, ...]:
        return tuple(d for d in self.dispositions if d.blocked)

    def _sum(self, attr: str, predicate=lambda d: True) -> Money:
        return sum_money(
            (getattr(d, attr) for d in self.calculated if predicate(d) and getattr(d, attr)),
            self.currency,
        )

    @property
    def total_proceeds(self) -> Money:
        return self._sum("proceeds")

    @property
    def short_term_gain(self) -> Money:
        return self._sum("gain", lambda d: d.character is Character.SHORT_TERM)

    @property
    def long_term_gain(self) -> Money:
        return self._sum("gain", lambda d: d.character is Character.LONG_TERM)

    @property
    def net_gain(self) -> Money:
        return self._sum("gain")

    @property
    def gross_gains(self) -> Money:
        """Gains only, losses excluded: the 'gross' reading of a gain budget."""
        return self._sum("gain", lambda d: d.gain is not None and not d.gain.is_negative)

    @property
    def gross_losses(self) -> Money:
        return self._sum("gain", lambda d: d.gain is not None and d.gain.is_negative)

    def to_json(self) -> Dict[str, Any]:
        return {
            "calculation_version": self.calculation_version,
            "rule_bundle_ref": self.rule_bundle_ref,
            "currency": self.currency,
            "dispositions": [d.to_json() for d in self.dispositions],
            "totals": {
                "total_proceeds": self.total_proceeds.to_json(),
                "short_term_gain": self.short_term_gain.to_json(),
                "long_term_gain": self.long_term_gain.to_json(),
                "net_gain": self.net_gain.to_json(),
                "gross_gains": self.gross_gains.to_json(),
                "gross_losses": self.gross_losses.to_json(),
            },
            "blocked_count": len(self.blocked),
        }


def _dispose(lot: TaxLot, quantity: Decimal, price: Money, sale_date: date,
             rules: TaxRules) -> LotDisposition:
    proceeds = price.times(quantity).quantized()
    if not lot.basis_known:
        return LotDisposition(
            lot_id=lot.lot_id, account_id=lot.account_id, security_id=lot.security_id,
            quantity=quantity, sale_date=sale_date, proceeds=proceeds, basis=None, gain=None,
            character=holding_period(lot.acquisition_date, sale_date, rules),
            holding_days=holding_days(lot.acquisition_date, sale_date),
            blocked=True, block_code=codes.MISSING_BASIS,
        )
    if lot.acquisition_date is None:
        basis = Money(lot.basis.amount * (quantity / lot.quantity), lot.basis.currency).quantized()
        return LotDisposition(
            lot_id=lot.lot_id, account_id=lot.account_id, security_id=lot.security_id,
            quantity=quantity, sale_date=sale_date, proceeds=proceeds, basis=basis, gain=None,
            character=Character.UNDETERMINED, holding_days=None,
            blocked=True, block_code=codes.MISSING_ACQUISITION_DATE,
        )

    basis = Money(lot.basis.amount * (quantity / lot.quantity), lot.basis.currency).quantized()
    return LotDisposition(
        lot_id=lot.lot_id, account_id=lot.account_id, security_id=lot.security_id,
        quantity=quantity, sale_date=sale_date, proceeds=proceeds, basis=basis,
        gain=(proceeds - basis).quantized(),
        character=holding_period(lot.acquisition_date, sale_date, rules),
        holding_days=holding_days(lot.acquisition_date, sale_date),
    )


def calculate_gain_loss(
    snapshot: Snapshot,
    sales: Sequence[ProposedSale],
    default_sale_date: Optional[date] = None,
    method: SelectionMethod = SelectionMethod.FIFO,
    rules: Optional[TaxRules] = None,
) -> GainLossResult:
    sale_date_default = default_sale_date or snapshot.as_of
    rules = rules or default_rules(sale_date_default.year)
    dispositions: List[LotDisposition] = []

    for sale in sales:
        sale_date = sale.sale_date or sale_date_default
        quantity = parse_quantity(sale.quantity)
        if sale.lot_id:
            lot = snapshot.lot(sale.lot_id)
            if lot is None:
                raise ValueError(f"lot {sale.lot_id} is not in snapshot {snapshot.snapshot_id}")
            if quantity > lot.quantity:
                raise ValueError(
                    f"lot {lot.lot_id} holds {lot.quantity}; cannot sell {quantity}")
            dispositions.append(
                _dispose(lot, quantity, sale.price_per_share, sale_date, rules))
            continue

        available = snapshot.lots_for(sale.account_id, sale.security_id)
        picked = select_lots(available, quantity, method, sale.price_per_share.amount)
        for lot, qty in picked:
            dispositions.append(_dispose(lot, qty, sale.price_per_share, sale_date, rules))

    return GainLossResult(tuple(dispositions), rule_bundle_ref=rules.bundle_ref)
