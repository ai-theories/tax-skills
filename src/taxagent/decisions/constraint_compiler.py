"""Compile an intent's constraints into an executable ConstraintSet.

The compiler refuses to guess. A realized-gain budget without a netting basis
or period is ambiguous, and guessing either one silently changes the answer, so
it raises AmbiguousConstraint and the run moves to NEEDS_INPUT.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Sequence

from ..domain.constraints import (AmbiguousConstraint, BudgetKind, ConstraintSet, GainBudget,
                                  NettingBasis, Restriction)
from ..domain.money import Money


def _money(value: Optional[str], currency: str) -> Optional[Money]:
    return None if value is None else Money.of(value, currency)


def compile_constraints(
    tenant_id: str,
    subject_id: str,
    intent: Dict[str, Any],
    restrictions: Sequence[Restriction] = (),
    rule_bundle_ref: str = "",
    constraints_ref: Optional[str] = None,
) -> ConstraintSet:
    unresolved: List[str] = []
    cash_target: Optional[Money] = None
    withdrawal_account_id: Optional[str] = None
    gain_budget: Optional[GainBudget] = None

    for objective in intent.get("objectives", []):
        if objective.get("type") == "raise_cash":
            amount = objective.get("amount")
            if amount is None:
                unresolved.append("objectives.raise_cash.amount")
            else:
                cash_target = Money.of(amount, objective.get("currency", "USD"))
            withdrawal_account_id = objective.get("withdrawal_account")
            if withdrawal_account_id is None:
                unresolved.append("withdrawal_account")
        elif objective.get("type") == "reduce_concentration":
            if objective.get("security_ref") is None:
                unresolved.append("security_ref")
            if objective.get("target_max_weight") is None:
                # "Reduce" without a target is not a bound the optimizer can honour.
                unresolved.append("target_max_weight")

    for constraint in intent.get("constraints", []):
        kind = constraint.get("type")
        if kind not in {BudgetKind.REALIZED_GAIN.value, BudgetKind.TAX_DOLLAR.value}:
            continue
        currency = constraint.get("currency", "USD")
        amount = constraint.get("amount")
        if amount is None:
            unresolved.append(f"{kind}.amount")
            continue
        netting = constraint.get("netting_basis")
        period = constraint.get("period")
        if netting is None:
            unresolved.append("netting_basis")
        if period is None:
            unresolved.append("period")
        if netting is None or period is None:
            continue
        gain_budget = GainBudget(
            kind=BudgetKind(kind),
            amount=Money.of(amount, currency),
            netting_basis=NettingBasis(netting),
            period=period,
            external_realized=_money(constraint.get("external_realized"), currency),
            loss_carryforward=_money(constraint.get("loss_carryforward"), currency),
            hard=constraint.get("hard", True),
        )

    if unresolved:
        raise AmbiguousConstraint(
            "Constraints cannot be compiled without these decisions: "
            + ", ".join(sorted(set(unresolved))),
            sorted(set(unresolved)),
        )

    return ConstraintSet(
        constraints_ref=constraints_ref or f"constraints_{uuid.uuid4().hex[:10]}",
        tenant_id=tenant_id,
        subject_id=subject_id,
        cash_target=cash_target,
        withdrawal_account_id=withdrawal_account_id,
        gain_budget=gain_budget,
        restrictions=tuple(restrictions),
        rule_bundle_ref=rule_bundle_ref,
    )
