"""Restriction evaluation implemented as tested code.

Deliberately not a rule DSL yet: a configurable authoring surface adds
versioning, review and migration cost, and is only worth it once non-engineers
need to author rules. The port stays stable if that changes.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Sequence

from ..domain import codes
from ..domain.constraints import ConstraintSet, Restriction
from ..domain.snapshots import Snapshot
from ..portfolio.lot_accounting import holding_days
from .interface import Decision


def evaluate_lot_restrictions(
    snapshot: Snapshot, constraints: ConstraintSet, as_of: date
) -> List[Decision]:
    decisions: List[Decision] = []
    for lot in snapshot.lots:
        blocked = False
        for restriction in constraints.restrictions:
            if lot.security_id in restriction.security_ids:
                decisions.append(Decision(
                    lot.lot_id, False, codes.RESTRICTED_SECURITY,
                    f"{lot.security_id} is restricted: {restriction.note or restriction.code}"))
                blocked = True
                break
            if lot.account_id in restriction.account_ids:
                decisions.append(Decision(
                    lot.lot_id, False, codes.RESTRICTED_ACCOUNT,
                    f"Account {lot.account_id} is restricted: "
                    f"{restriction.note or restriction.code}"))
                blocked = True
                break
            if restriction.min_holding_days is not None:
                held = holding_days(lot.acquisition_date, as_of)
                if held is not None and held < restriction.min_holding_days:
                    decisions.append(Decision(
                        lot.lot_id, False, codes.MIN_HOLDING_PERIOD,
                        f"Held {held} days; policy requires {restriction.min_holding_days}."))
                    blocked = True
                    break
        if not blocked:
            decisions.append(Decision(lot.lot_id, True, "ALLOWED", "No restriction matched."))
    return decisions
