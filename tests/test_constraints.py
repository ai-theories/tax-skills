import pytest

from taxagent.decisions.constraint_compiler import compile_constraints
from taxagent.domain.constraints import AmbiguousConstraint, NettingBasis


def _intent(**overrides):
    constraint = {"type": "realized_gain_budget", "amount": "15000.00", "currency": "USD",
                  "netting_basis": "net_gains", "period": "2026"}
    constraint.update(overrides)
    return {"objectives": [], "constraints": [constraint]}


def test_gain_budget_without_netting_basis_is_ambiguous():
    # Gross and net readings of "keep gains under $15,000" give different
    # answers, so the compiler asks rather than picking one.
    with pytest.raises(AmbiguousConstraint) as excinfo:
        compile_constraints("t", "s", _intent(netting_basis=None))
    assert "netting_basis" in excinfo.value.unresolved_fields


def test_gain_budget_without_period_is_ambiguous():
    with pytest.raises(AmbiguousConstraint) as excinfo:
        compile_constraints("t", "s", _intent(period=None))
    assert "period" in excinfo.value.unresolved_fields


def test_reduce_concentration_without_a_target_is_ambiguous():
    intent = {"objectives": [{"type": "reduce_concentration", "security_ref": None}],
              "constraints": []}
    with pytest.raises(AmbiguousConstraint) as excinfo:
        compile_constraints("t", "s", intent)
    assert {"security_ref", "target_max_weight"} <= set(excinfo.value.unresolved_fields)


def test_raise_cash_without_withdrawal_account_is_ambiguous():
    intent = {"objectives": [{"type": "raise_cash", "amount": "50000.00"}], "constraints": []}
    with pytest.raises(AmbiguousConstraint) as excinfo:
        compile_constraints("t", "s", intent)
    assert "withdrawal_account" in excinfo.value.unresolved_fields


def test_complete_constraints_compile():
    constraints = compile_constraints("t", "s", _intent(external_realized="4000.00"))
    assert constraints.gain_budget.netting_basis is NettingBasis.NET_GAINS
    assert constraints.gain_budget.remaining.to_json() == "11000.00"
