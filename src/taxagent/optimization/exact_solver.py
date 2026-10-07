"""Exact lot selection with a certificate, and a brute-force reference.

Every optimizer in this package reduces to the same question: which lots do we
sell, given a gain budget, to raise the cash we need. That is a knapsack, and
knapsacks are solvable exactly at the sizes a taxable account actually has.

## Why a certificate rather than a claim

`solver_status: optimal` is the single most dangerous string this codebase can
emit. Downstream it licenses language — "this is the best available" — that
nothing else in the system would be allowed to use. So optimality is only
reported when the search provably exhausted the space: every node either
explored or pruned by a bound that cannot cut off an improving solution. When
a limit stops the search, the status degrades to `feasible` and the gap
between incumbent and bound is reported as a number.

## The objective is lexicographic, and the order matters

1. **Reach the cash target.** Cash beyond it serves no stated objective, so
   the first key is cash capped at the target. Without the cap an optimizer
   would liquidate a portfolio to "maximise" something nobody asked for.
2. **Realize as little gain as possible.** Two plans that both raise the cash
   are not equally good; the one that triggers less tax is better, and a
   budget is a limit rather than an allowance to spend.
3. **Use as few trades as possible.** Every trade has costs this engine does
   not model — commissions, spread, operational risk — so where two plans tie
   on the first two keys, the smaller one wins.
4. **Lot id.** Determinism. The same inputs must give the same plan, because
   evidence replay depends on it.

## What exactness does not mean

The solver is exact for the model it is given. It does not know that a
security is about to be downgraded, that the client has a view on it, or that
the lots were chosen for a reason. Optimal here means optimal over the
constraints supplied, nothing more, and the engines that use it say so.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..domain.constraints import NettingBasis

#: Beyond this many budget-consuming lots the search is not attempted
#: exhaustively. Chosen so a pathological instance degrades to an honest
#: `feasible` with a reported gap rather than hanging a request.
DEFAULT_NODE_LIMIT = 200_000
#: Above this many gain lots, branch and bound is skipped entirely and the
#: greedy incumbent is returned with its gap. Exhausting 2^40 nodes is not a
#: thing that finishes, and pretending otherwise would be the defect.
MAX_EXHAUSTIVE_ITEMS = 40


@dataclass(frozen=True)
class SolverItem:
    """One sellable lot, reduced to the two numbers the knapsack needs."""

    lot_id: str
    cash: Decimal
    gain: Decimal
    account_id: str = ""
    unit_id: str = ""

    @property
    def is_gain(self) -> bool:
        return self.gain > 0

    def budget_cost(self, netting: NettingBasis) -> Decimal:
        """What selling this lot consumes from the budget.

        Under gross netting a loss consumes nothing and returns nothing. Under
        net netting it returns capacity, which is the whole mechanism by which
        a harvest funds a gain elsewhere.
        """
        if netting is NettingBasis.GROSS_GAINS:
            return self.gain if self.gain > 0 else Decimal("0")
        return self.gain


@dataclass(frozen=True)
class SolveOutcome:
    chosen: Tuple[str, ...]
    cash: Decimal
    gross_gains: Decimal
    net_gain: Decimal
    trade_count: int
    status: str                      # optimal | feasible | infeasible
    bound_cash: Decimal
    nodes_explored: int
    search_exhausted: bool
    reason: str = ""
    certificate: str = ""
    #: Cash as the objective counts it: capped at the target, because raising
    #: more than was asked for is not an improvement. The bound is capped the
    #: same way, so the two are comparable. Comparing a capped bound against
    #: uncapped cash produced a negative gap, which looked like an inadmissible
    #: bound and would have masked a real one.
    objective_cash: Decimal = Decimal("0")

    @property
    def gap(self) -> Decimal:
        return max(Decimal("0"), self.bound_cash - self.objective_cash)

    def to_json(self) -> Dict[str, Any]:
        return {
            "chosen": list(self.chosen),
            "cash": str(self.cash), "gross_gains": str(self.gross_gains),
            "net_gain": str(self.net_gain), "trade_count": self.trade_count,
            "status": self.status, "bound_cash": str(self.bound_cash),
            "objective_cash": str(self.objective_cash),
            "gap": str(self.gap), "nodes_explored": self.nodes_explored,
            "search_exhausted": self.search_exhausted,
            "certificate": self.certificate, "reason": self.reason,
        }


def _objective(cash: Decimal, gross: Decimal, count: int,
               cash_target: Optional[Decimal]) -> Tuple[Decimal, Decimal, int]:
    """The lexicographic key, as a comparable tuple.

    Cash is capped at the target so that raising more than was asked for is
    not treated as an improvement. Gross gains and trade count are negated by
    the caller's comparison, which prefers smaller.
    """
    effective = min(cash, cash_target) if cash_target is not None else cash
    return (effective, -gross, -count)


def _fractional_bound(remaining: Sequence[SolverItem], capacity: Decimal,
                      netting: NettingBasis) -> Decimal:
    """Cash still reachable if lots were divisible: the LP relaxation.

    This is admissible — it never understates — because any integral solution
    is also a fractional one. That is what makes pruning on it safe.
    """
    total = Decimal("0")
    free = [i for i in remaining if i.budget_cost(netting) <= 0]
    for item in free:
        total += item.cash
        capacity -= item.budget_cost(netting)   # a negative cost returns capacity

    costly = sorted((i for i in remaining if i.budget_cost(netting) > 0),
                    key=lambda i: (-(i.cash / i.budget_cost(netting)), i.lot_id))
    for item in costly:
        if capacity <= 0:
            break
        cost = item.budget_cost(netting)
        take = min(Decimal("1"), capacity / cost)
        total += item.cash * take
        capacity -= cost * take
    return total


def solve(items: Sequence[SolverItem], capacity: Decimal, netting: NettingBasis,
          cash_target: Optional[Decimal] = None,
          node_limit: int = DEFAULT_NODE_LIMIT) -> SolveOutcome:
    """Choose lots to sell, exactly where the space can be exhausted.

    `capacity` is the gain budget already net of anything realized elsewhere.

    The budget constrains the *final* selection, not every prefix of it. Under
    net netting a loss lot has a negative cost and so returns capacity, which
    means a partial selection may breach the budget and a larger one satisfy
    it. Checking feasibility lot by lot therefore discards valid plans — it
    did, on 76 of 4,000 random instances, until the feasibility test moved to
    the leaf and pruning moved to the minimum cost still reachable.
    """
    items = sorted(items, key=lambda i: i.lot_id)
    if not items:
        return SolveOutcome((), Decimal("0"), Decimal("0"), Decimal("0"), 0,
                            "infeasible" if cash_target and cash_target > 0 else "optimal",
                            Decimal("0"), 0, True, objective_cash=Decimal("0"),
                            reason=("No lot in this account may be sold, so no cash can "
                                    "be raised at all." if cash_target else ""),
                            certificate="empty instance: the only plan is to do nothing")

    # Relaxing lots first: they add cash and, under net netting, capacity.
    # Consuming lots after, by cash per budget dollar, so the bound bites early.
    ordered = sorted(items, key=lambda i: (
        i.budget_cost(netting) > 0,
        -(i.cash / i.budget_cost(netting)) if i.budget_cost(netting) > 0 else Decimal("0"),
        i.lot_id))

    # Suffix minimum cost: the least the remaining lots can add to the running
    # total, which is the sum of their negative costs. A subtree whose best
    # possible final cost still breaches the budget contains no feasible plan.
    suffix_min: List[Decimal] = [Decimal("0")] * (len(ordered) + 1)
    for index in range(len(ordered) - 1, -1, -1):
        cost = ordered[index].budget_cost(netting)
        suffix_min[index] = suffix_min[index + 1] + (cost if cost < 0 else Decimal("0"))

    if suffix_min[0] > capacity:
        # Even selling every capacity-returning lot breaches the budget, so no
        # selection at all is feasible — not even the empty one.
        return SolveOutcome(
            (), Decimal("0"), Decimal("0"), Decimal("0"), 0, "infeasible",
            Decimal("0"), 0, True, objective_cash=Decimal("0"),
            reason=("The gain budget is already exhausted before this run: realized "
                    "gains elsewhere exceed it, so no sale here is within it."),
            certificate="proven at the root: the minimum reachable gain exceeds the budget")

    costly_count = sum(1 for i in ordered if i.budget_cost(netting) > 0)
    if costly_count > MAX_EXHAUSTIVE_ITEMS:
        return _greedy_with_gap(ordered, capacity, netting, cash_target)

    best: Dict[str, Any] = {
        "key": None, "chosen": (), "cash": Decimal("0"),
        "gross": Decimal("0"), "net": Decimal("0"),
    }
    nodes = 0
    exhausted = True

    def descend(index: int, chosen: Tuple[str, ...], cash: Decimal, gross: Decimal,
                net: Decimal, cost: Decimal) -> None:
        nonlocal nodes, exhausted
        nodes += 1
        if nodes > node_limit:
            exhausted = False
            return

        # Record only feasible selections. Infeasible prefixes stay in the
        # search because a later capacity-returning lot can rescue them.
        if cost <= capacity:
            key = _objective(cash, gross, len(chosen), cash_target)
            if best["key"] is None or key > best["key"]:
                best.update({"key": key, "chosen": chosen, "cash": cash,
                             "gross": gross, "net": net})

        if index >= len(ordered):
            return

        # No feasible completion exists below here.
        if cost + suffix_min[index] > capacity:
            return

        # Cash still reachable, given the capacity left at this node.
        reachable = cash + _fractional_bound(ordered[index:], capacity - cost, netting)
        capped = min(reachable, cash_target) if cash_target is not None else reachable
        if best["key"] is not None and capped < best["key"][0]:
            return

        item = ordered[index]
        item_cost = item.budget_cost(netting)
        descend(index + 1, chosen + (item.lot_id,), cash + item.cash,
                gross + (item.gain if item.gain > 0 else Decimal("0")),
                net + item.gain, cost + item_cost)
        descend(index + 1, chosen, cash, gross, net, cost)

    descend(0, (), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))

    bound = _fractional_bound(ordered, capacity, netting)
    if cash_target is not None:
        bound = min(bound, cash_target)

    chosen = tuple(sorted(best["chosen"]))
    cash = best["cash"]
    capped = min(cash, cash_target) if cash_target is not None else cash
    if cash_target is not None and cash < cash_target:
        return SolveOutcome(
            chosen, cash, best["gross"], best["net"], len(chosen), "infeasible",
            bound, nodes, exhausted, objective_cash=capped,
            reason=("The cash target cannot be reached within the gain budget. The "
                    f"most any selection raises is {cash}, short of {cash_target}. "
                    "Raising the budget or lowering the target are the only ways "
                    "through; neither is done automatically."),
            certificate=("exhaustive search: every selection was enumerated or pruned "
                         "by an admissible bound" if exhausted else
                         "search truncated at the node limit; infeasibility is not proven"))

    status = "optimal" if exhausted else "feasible"
    return SolveOutcome(
        chosen, cash, best["gross"], best["net"], len(chosen), status, bound,
        nodes, exhausted, objective_cash=capped,
        certificate=("exhaustive branch and bound: every selection was either "
                     "enumerated or pruned by an admissible LP bound, so no better "
                     "selection exists under these constraints"
                     if exhausted else
                     "search stopped at the node limit; the reported gap is the "
                     "distance to a relaxed bound, not a reachable improvement"))


def _greedy_with_gap(ordered: Sequence[SolverItem], capacity: Decimal,
                     netting: NettingBasis,
                     cash_target: Optional[Decimal]) -> SolveOutcome:
    """Too large to exhaust: take the greedy incumbent and report the gap.

    Honest degradation. The status is never `optimal` here, whatever the gap
    turns out to be. Capacity-returning lots are taken first so the budget they
    free is available to the lots that consume it.
    """
    chosen: List[str] = []
    cash = gross = net = cost = Decimal("0")
    for item in ordered:
        if item.budget_cost(netting) <= 0:
            chosen.append(item.lot_id)
            cash += item.cash
            net += item.gain
            cost += item.budget_cost(netting)
    for item in ordered:
        item_cost = item.budget_cost(netting)
        if item_cost <= 0:
            continue
        if cash_target is not None and cash >= cash_target:
            break
        if cost + item_cost > capacity:
            continue
        chosen.append(item.lot_id)
        cash += item.cash
        net += item.gain
        gross += item.gain
        cost += item_cost

    bound = _fractional_bound(ordered, capacity, netting)
    if cash_target is not None:
        bound = min(bound, cash_target)
    status = "feasible"
    reason = ""
    if cost > capacity:
        status = "infeasible"
        reason = "Even the capacity-returning lots alone breach the budget."
    elif cash_target is not None and cash < cash_target:
        status = "infeasible"
        reason = (f"Greedy selection raises {cash} against a target of {cash_target}. "
                  "The instance is too large to search exhaustively, so this is a "
                  "failure to find a plan rather than proof that none exists.")
    return SolveOutcome(
        tuple(sorted(chosen)), cash, gross, net, len(chosen), status, bound, 0, False,
        objective_cash=(min(cash, cash_target) if cash_target is not None else cash),
        reason=reason,
        certificate=(f"instance exceeds {MAX_EXHAUSTIVE_ITEMS} budget-consuming lots; "
                     "greedy incumbent with an LP gap, no optimality claimed"))


# --- reference implementation ---------------------------------------------

def brute_force(items: Sequence[SolverItem], capacity: Decimal, netting: NettingBasis,
                cash_target: Optional[Decimal] = None) -> SolveOutcome:
    """Every subset, compared directly. Exponential, and that is the point.

    This exists so the branch-and-bound solver can be checked against an
    implementation with no bounding, no ordering and no pruning — nothing that
    could be subtly wrong in the same way. The tests run both on every small
    instance and require identical objective values.
    """
    if len(items) > 20:
        raise ValueError("brute_force is for verification on small instances only")

    best_key: Optional[Tuple[Decimal, Decimal, int]] = None
    best: Tuple[str, ...] = ()
    best_cash = best_gross = best_net = Decimal("0")
    any_feasible = False

    for size in range(len(items) + 1):
        for combo in itertools.combinations(sorted(items, key=lambda i: i.lot_id), size):
            cost = sum((i.budget_cost(netting) for i in combo), Decimal("0"))
            if cost > capacity:
                continue
            cash = sum((i.cash for i in combo), Decimal("0"))
            gross = sum((i.gain for i in combo if i.gain > 0), Decimal("0"))
            net = sum((i.gain for i in combo), Decimal("0"))
            any_feasible = True
            key = _objective(cash, gross, len(combo), cash_target)
            if best_key is None or key > best_key:
                best_key = key
                best = tuple(i.lot_id for i in combo)
                best_cash, best_gross, best_net = cash, gross, net

    # No subset at all satisfies the budget. That happens when a gross-gains
    # budget is already breached by gains realized elsewhere: gross gains
    # cannot go below zero, so every plan including selling nothing is over.
    status = "optimal"
    if not any_feasible:
        status = "infeasible"
    elif cash_target is not None and best_cash < cash_target:
        status = "infeasible"
    return SolveOutcome(
        tuple(sorted(best)), best_cash, best_gross, best_net, len(best), status,
        best_cash, 2 ** len(items), True,
        objective_cash=(min(best_cash, cash_target) if cash_target is not None
                        else best_cash),
        certificate="exhaustive enumeration of every subset")


def declined_harvests(items: Sequence[SolverItem], outcome: SolveOutcome,
                      netting: NettingBasis) -> List[Dict[str, str]]:
    """Loss lots the plan passed over, and what harvesting them was worth.

    The objective prefers fewer trades once the cash target is met, so a loss
    that costs nothing in budget can still be left behind. That is a defensible
    trade-off and an invisible one: an advisor reading "optimal" would not
    expect an available harvest to have been declined. Reporting it lets them
    override a preference the engine cannot weigh — transaction costs it does
    not know against a tax benefit it does.
    """
    chosen = set(outcome.chosen)
    declined = []
    for item in sorted(items, key=lambda i: i.lot_id):
        if item.lot_id in chosen or item.gain >= 0:
            continue
        if item.budget_cost(netting) > 0:
            continue
        declined.append({
            "lot_id": item.lot_id,
            "loss_foregone": str(-item.gain),
            "cash_foregone": str(item.cash),
            "reason": "The cash target was already met and the objective prefers fewer "
                      "trades. Harvesting it costs nothing against the gain budget.",
        })
    return declined


def gap_report(outcome: "SolveOutcome", method: str, interpretation: str,
               bound: Optional[Decimal] = None,
               achieved: Optional[Decimal] = None) -> Dict[str, Any]:
    """The optimality_gap block, in the shape every reader expects.

    The report renderer and the console both read `gap_percent`. An engine
    that published a gap without it crashed the run that produced it, which is
    why the block is built here rather than assembled per engine.
    """
    bound_cash = outcome.bound_cash if bound is None else bound
    achieved_cash = outcome.objective_cash if achieved is None else achieved
    gap = max(Decimal("0"), bound_cash - achieved_cash)
    percent = None
    if bound_cash > 0:
        percent = f"{((gap / bound_cash) * Decimal('100')).quantize(Decimal('0.01'))}%"
    return {
        "method": method,
        "bound_cash": str(bound_cash),
        "achieved_cash": str(achieved_cash),
        "gap": str(gap),
        "gap_percent": percent,
        "provably_optimal": outcome.status == "optimal" and gap == 0,
        "interpretation": interpretation,
    }
