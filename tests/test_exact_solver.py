"""The exact solver, checked against an implementation that cannot be clever.

Branch and bound is where an optimizer goes subtly wrong: a bound that is not
admissible, a prune that cuts an improving branch, a feasibility test applied
at the wrong moment. None of those produce a crash. They produce a slightly
worse answer, reported as optimal.

So the solver is checked against exhaustive enumeration — no ordering, no
bounding, no pruning, nothing that could be wrong in the same way — on
thousands of randomly generated instances, including the awkward ones:
negative capacity, capacity-returning lots, and targets that cannot be met.

That cross-check found a real defect. The first implementation enforced the
budget at every prefix of the search, which is wrong under net netting because
a loss lot has a negative cost and returns capacity: a partial selection may
breach the budget where the full one satisfies it. It disagreed with brute
force on 76 of 4,000 instances.
"""
import random
from decimal import Decimal

import pytest

from taxagent.domain.constraints import NettingBasis
from taxagent.optimization.exact_solver import (MAX_EXHAUSTIVE_ITEMS, SolverItem,
                                                _objective, brute_force, solve)

GROSS = NettingBasis.GROSS_GAINS
NET = NettingBasis.NET_GAINS


def _instance(rng, max_lots=8):
    n = rng.randint(1, max_lots)
    items = [SolverItem(f"lot_{i:02d}",
                        Decimal(rng.randint(1, 60) * 100),
                        Decimal(rng.randint(-40, 40) * 100))
             for i in range(n)]
    capacity = Decimal(rng.randint(-20, 60) * 100)
    netting = rng.choice([GROSS, NET])
    target = rng.choice([None, Decimal(rng.randint(0, 200) * 100)])
    return items, capacity, netting, target


@pytest.mark.parametrize("seed", [20261006, 7, 991, 40427])
def test_matches_brute_force_on_random_instances(seed):
    """The core guarantee. Any disagreement is a defect in the solver."""
    rng = random.Random(seed)
    for trial in range(1500):
        items, capacity, netting, target = _instance(rng)
        fast = solve(items, capacity, netting, target)
        slow = brute_force(items, capacity, netting, target)
        assert (_objective(fast.cash, fast.gross_gains, fast.trade_count, target)
                == _objective(slow.cash, slow.gross_gains, slow.trade_count, target)), (
            f"seed {seed} trial {trial}: {[(i.lot_id, i.cash, i.gain) for i in items]} "
            f"capacity={capacity} netting={netting.value} target={target}")
        assert (fast.status == "infeasible") == (slow.status == "infeasible")


@pytest.mark.parametrize("seed", [11, 12345])
def test_a_reported_optimum_is_really_feasible(seed):
    """An optimal answer that breaches the budget would be the worst outcome."""
    rng = random.Random(seed)
    for _ in range(1500):
        items, capacity, netting, target = _instance(rng)
        outcome = solve(items, capacity, netting, target)
        chosen = {i.lot_id for i in items if i.lot_id in outcome.chosen}
        cost = sum((i.budget_cost(netting) for i in items if i.lot_id in chosen),
                   Decimal("0"))
        if outcome.chosen:
            assert cost <= capacity, "the selection breaches the budget it was given"
        cash = sum((i.cash for i in items if i.lot_id in chosen), Decimal("0"))
        assert cash == outcome.cash
        gross = sum((i.gain for i in items if i.lot_id in chosen and i.gain > 0),
                    Decimal("0"))
        assert gross == outcome.gross_gains


def test_the_bound_is_never_below_what_was_achieved():
    """An admissible bound is the premise of every prune.

    If it understates, the search can cut off the answer and still report
    optimality. The comparison is against the objective's own measure of cash
    — capped at the target, as the bound is — because comparing a capped bound
    against uncapped cash produces a spurious negative gap that would mask a
    real one.
    """
    rng = random.Random(555)
    for _ in range(2000):
        items, capacity, netting, target = _instance(rng)
        outcome = solve(items, capacity, netting, target)
        assert outcome.bound_cash >= outcome.objective_cash - Decimal("0.000001")
        assert outcome.gap >= 0
        if target is not None:
            assert outcome.objective_cash == min(outcome.cash, target)


def test_optimal_is_only_claimed_when_the_search_exhausted():
    rng = random.Random(99)
    for _ in range(800):
        items, capacity, netting, target = _instance(rng)
        outcome = solve(items, capacity, netting, target)
        if outcome.status == "optimal":
            assert outcome.search_exhausted
            assert "exhaustive" in outcome.certificate or "empty" in outcome.certificate


def test_a_node_limit_degrades_to_feasible_rather_than_lying():
    """Stopping early must change the claim, not just the runtime."""
    items = [SolverItem(f"lot_{i:02d}", Decimal(100 + i), Decimal(50 + i))
             for i in range(18)]
    outcome = solve(items, Decimal("400"), GROSS, None, node_limit=5)
    assert outcome.status != "optimal"
    assert not outcome.search_exhausted
    assert "node limit" in outcome.certificate


def test_a_large_instance_is_not_attempted_exhaustively():
    items = [SolverItem(f"lot_{i:03d}", Decimal(1000), Decimal(100))
             for i in range(MAX_EXHAUSTIVE_ITEMS + 5)]
    outcome = solve(items, Decimal("2500"), GROSS, None)
    assert outcome.status != "optimal"
    assert "no optimality claimed" in outcome.certificate
    # It still returns a usable plan within the budget.
    chosen = {i.lot_id for i in items if i.lot_id in outcome.chosen}
    assert sum((Decimal("100") for _ in chosen), Decimal("0")) <= Decimal("2500")


def test_a_loss_lot_returns_capacity_under_net_netting():
    """The case the first implementation got wrong.

    A zero budget admits a 5,000 gain only because a 5,000 loss is sold with
    it. Enforcing the budget lot by lot rejects the gain before the loss is
    reached, and the plan is lost.
    """
    items = [SolverItem("lot_gain", Decimal("15000"), Decimal("5000")),
             SolverItem("lot_loss", Decimal("6000"), Decimal("-5000"))]
    outcome = solve(items, Decimal("0"), NET, Decimal("21000"))
    assert outcome.status == "optimal"
    assert set(outcome.chosen) == {"lot_gain", "lot_loss"}
    assert outcome.cash == Decimal("21000")
    assert outcome.net_gain == Decimal("0")


def test_the_same_loss_does_not_help_under_gross_netting():
    """Gross netting counts gains alone, so the loss returns nothing."""
    items = [SolverItem("lot_gain", Decimal("15000"), Decimal("5000")),
             SolverItem("lot_loss", Decimal("6000"), Decimal("-5000"))]
    outcome = solve(items, Decimal("0"), GROSS, Decimal("21000"))
    assert outcome.status == "infeasible"
    assert outcome.chosen == ("lot_loss",)
    assert "cannot be reached" in outcome.reason


def test_an_already_breached_gross_budget_refuses_at_the_root():
    items = [SolverItem("lot_loss", Decimal("6000"), Decimal("-5000"))]
    outcome = solve(items, Decimal("-2000"), GROSS, None)
    assert outcome.status == "infeasible"
    assert "already exhausted" in outcome.reason
    assert outcome.chosen == ()


def test_the_objective_prefers_less_gain_over_more_cash_once_the_target_is_met():
    """Cash beyond the target is not an improvement, and gain is a cost."""
    items = [SolverItem("lot_cheap", Decimal("15000"), Decimal("1000")),
             SolverItem("lot_dear", Decimal("15000"), Decimal("9000"))]
    outcome = solve(items, Decimal("20000"), GROSS, Decimal("15000"))
    assert outcome.chosen == ("lot_cheap",)
    assert outcome.gross_gains == Decimal("1000")


def test_the_objective_prefers_fewer_trades_on_a_tie():
    items = [SolverItem("lot_one", Decimal("6000"), Decimal("0")),
             SolverItem("lot_two_a", Decimal("3000"), Decimal("0")),
             SolverItem("lot_two_b", Decimal("3000"), Decimal("0"))]
    outcome = solve(items, Decimal("0"), GROSS, Decimal("6000"))
    assert outcome.trade_count == 1
    assert outcome.chosen == ("lot_one",)


def test_the_same_instance_always_gives_the_same_plan():
    """Evidence replay depends on it."""
    rng = random.Random(2024)
    for _ in range(300):
        items, capacity, netting, target = _instance(rng)
        first = solve(items, capacity, netting, target)
        shuffled = list(items)
        random.Random(7).shuffle(shuffled)
        second = solve(shuffled, capacity, netting, target)
        assert first.chosen == second.chosen


def test_brute_force_refuses_instances_it_cannot_enumerate():
    items = [SolverItem(f"lot_{i}", Decimal("1"), Decimal("1")) for i in range(21)]
    with pytest.raises(ValueError):
        brute_force(items, Decimal("5"), GROSS, None)


# --- scale -----------------------------------------------------------------
# A direct-index sleeve holds hundreds of lots, not the handful the fixtures
# use. The degradation path was implemented and never exercised at that size.

def test_a_direct_index_sized_account_still_returns_a_usable_plan():
    """200 lots: past the exhaustive limit, so a plan with an honest gap.

    What must hold at this size is everything except the optimality claim: the
    plan is inside the budget, the cash is real, and the status never says
    optimal.
    """
    rng = random.Random(8080)
    items = [SolverItem(f"lot_{i:03d}",
                        Decimal(rng.randint(500, 5_000)),
                        Decimal(rng.randint(-2_000, 2_000)))
             for i in range(200)]
    outcome = solve(items, Decimal("25000"), GROSS, Decimal("150000"))

    assert outcome.status != "optimal"
    assert not outcome.search_exhausted
    chosen = {i.lot_id: i for i in items if i.lot_id in outcome.chosen}
    cost = sum((i.budget_cost(GROSS) for i in chosen.values()), Decimal("0"))
    assert cost <= Decimal("25000"), "the plan breaches the budget it was given"
    assert sum((i.cash for i in chosen.values()), Decimal("0")) == outcome.cash
    assert outcome.gap >= 0


def test_scale_degradation_is_gradual_not_a_cliff():
    """The plan should not collapse as the instance grows past the limit.

    A greedy fallback that returned almost nothing would be technically honest
    and practically useless, so the cash it raises is compared against the
    relaxed bound rather than only checked for feasibility.
    """
    rng = random.Random(4321)
    for count in (30, 60, 120, 240):
        items = [SolverItem(f"lot_{i:03d}", Decimal(rng.randint(500, 5_000)),
                            Decimal(rng.randint(-1_000, 2_000)))
                 for i in range(count)]
        outcome = solve(items, Decimal("20000"), GROSS, None)
        assert outcome.bound_cash > 0
        captured = outcome.cash / outcome.bound_cash
        assert captured >= Decimal("0.5"), (
            f"{count} lots: captured only {captured:.0%} of the relaxed bound")


def test_a_large_instance_is_still_deterministic():
    """Replay depends on it, and the fallback path orders lots differently."""
    rng = random.Random(99)
    items = [SolverItem(f"lot_{i:03d}", Decimal(rng.randint(500, 5_000)),
                        Decimal(rng.randint(-1_000, 2_000)))
             for i in range(150)]
    first = solve(items, Decimal("18000"), NET, Decimal("90000"))
    shuffled = list(items)
    random.Random(7).shuffle(shuffled)
    second = solve(shuffled, Decimal("18000"), NET, Decimal("90000"))
    assert first.chosen == second.chosen
