"""Tax-aware rebalancing to target weights.

Rebalancing and harvesting pull in opposite directions. A harvest wants to
realize losses; a rebalance wants to reach a target allocation and would
rather realize nothing at all. Pricing one with the other's engine optimises
the wrong objective, which is why this release refused rebalancing until a
separate engine existed rather than reaching for the harvest selector.

## The drift model, stated because it constrains the answer

Drift is the sum of absolute differences between current and target weight,
measured in fractions of portfolio market value. The engine never sells more
of a security than its overweight: overshooting would close drift on one side
and open it on the other, and forbidding it keeps the objective linear, which
is what makes the selection exactly solvable.

The consequence is honest and worth stating to whoever reads the result: with
chunky lots the engine under-trades rather than overshoots, so residual drift
is expected and is reported rather than hidden.

## Why buys are not lot-level

A purchase has no tax consequence, so there is nothing to optimise about which
lot it becomes. Proceeds are allocated to underweight securities in proportion
to their shortfall. What *is* checked is whether a buy would wash a
loss already realized inside the window. A rebalance that buys back into a
position sold at a loss three weeks ago disallows that loss, and the client
sees neither the loss nor a warning. Within a single run the risk cannot
arise, because a security is either overweight or underweight and the engine
never sells past target; the exposure is entirely to sales that happened
before the run, which is why the account's own transaction history is read.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import CALCULATION_VERSIONS
from ..domain import codes
from ..domain.constraints import AmbiguousConstraint, NettingBasis
from ..domain.money import Money
from ..domain.tax_lots import Action
from . import exact_solver
from .eligibility import eligible_items, trades_for
from .interface import Candidate, CandidateTrade, OptimizationProblem, UnsupportedCapability

#: Beyond this many candidate sells the per-security search is not exhausted.
MAX_EXHAUSTIVE_LOTS = 24


class TaxAwareRebalanceEngine:
    name = "rebalance-engine"
    version = CALCULATION_VERSIONS["rebalance_engine"]
    supported_scopes = ("account",)
    claims_optimality = True

    def __init__(self, node_limit: int = exact_solver.DEFAULT_NODE_LIMIT):
        self.node_limit = node_limit

    def solve(self, problem: OptimizationProblem) -> Candidate:
        if problem.scope != "account":
            raise UnsupportedCapability(
                codes.UNSUPPORTED_SCOPE,
                "This engine rebalances one account. A household rebalance would have "
                "to decide which account holds which exposure, which is an allocation "
                "decision this engine does not make.",
                supported_alternative="account")

        target = problem.constraints.target_allocation
        if target is None:
            raise UnsupportedCapability(
                codes.UNSUPPORTED_SCOPE,
                "A rebalance needs target weights. Without them there is nothing to "
                "measure drift against, and inferring a target from the current "
                "holdings would make the portfolio its own benchmark.",
                supported_alternative="harvest-review for a cash or loss objective")
        target.validate()

        snapshot = problem.snapshot
        prices = problem.prices
        values, unpriced = self._market_values(snapshot, prices)
        total = sum(values.values(), Decimal("0"))
        if total <= 0:
            raise UnsupportedCapability(
                codes.UNSUPPORTED_SCOPE,
                "The account has no priced market value, so no weight can be computed.")

        securities = sorted(set(values) | {s for s, _ in target.weights})
        before = {s: (values.get(s, Decimal("0")) / total) for s in securities}
        drift_before = self._drift(before, target, securities)

        # How much of each security is above target, in money.
        excess = {
            s: max(Decimal("0"), values.get(s, Decimal("0")) - target.weight_for(s) * total)
            for s in securities
        }
        shortfall = {
            s: max(Decimal("0"), target.weight_for(s) * total - values.get(s, Decimal("0")))
            for s in securities
        }
        tolerance_money = target.tolerance * total
        for security in securities:
            if excess[security] <= tolerance_money:
                excess[security] = Decimal("0")
            if shortfall[security] <= tolerance_money:
                shortfall[security] = Decimal("0")

        items, skipped, flagged = eligible_items(problem)
        by_security: Dict[str, List[exact_solver.SolverItem]] = defaultdict(list)
        lot_security = {lot.lot_id: lot.security_id for lot in snapshot.lots}
        for item in items:
            security = lot_security[item.lot_id]
            if excess.get(security, Decimal("0")) > 0:
                by_security[security].append(item)
            else:
                skipped.append({"lot_id": item.lot_id, "reason": "NOT_OVERWEIGHT"})

        budget = problem.constraints.gain_budget
        netting = budget.netting_basis if budget else NettingBasis.NET_GAINS
        capacity = (budget.remaining.amount if budget
                    else sum((i.gain for i in items if i.gain > 0), Decimal("0")))

        chosen, per_security, exhausted = self._select(
            by_security, excess, capacity, netting)

        sold_value = sum((i.cash for s in per_security.values() for i in s["items"]),
                         Decimal("0"))
        gross = sum((i.gain for s in per_security.values() for i in s["items"]
                     if i.gain > 0), Decimal("0"))
        net = sum((i.gain for s in per_security.values() for i in s["items"]),
                  Decimal("0"))

        # A buy must not wash a loss. Within one run that cannot happen — a
        # security is either overweight or underweight, and the engine never
        # sells past target — so the reachable risk is a loss already realized
        # inside the window, before this run. That is what is screened.
        washing = self._recent_loss_securities(problem)
        buys, blocked_buys = self._allocate(shortfall, sold_value, prices, washing)

        after = self._weights_after(values, total, per_security, buys, lot_security)
        drift_after = self._drift(after, target, securities)

        status = "optimal" if exhausted else "feasible"
        diagnostics: Dict[str, Any] = {
            "objective": "minimise_drift",
            "selection_rule": ("exact branch and bound per overweight security, "
                               "never selling past target"),
            "optimality_claimed": exhausted,
            "drift_before": str(drift_before),
            "drift_after": str(drift_after),
            "drift_closed": str(drift_before - drift_after),
            "tolerance": str(target.tolerance),
            "total_market_value": str(total),
            "proceeds": str(sold_value),
            "gross_realized_gains": str(gross),
            "net_realized_gain": str(net),
            "cash_raised": str(sold_value - sum((b["amount"] for b in buys),
                                                Decimal("0"))),
            "lots_considered": len(items),
            "lots_skipped": skipped,
            "wash_sale_flagged": flagged,
            "unpriced_securities": sorted(unpriced),
            "per_security": [
                {"security_id": security,
                 "excess": str(excess.get(security, Decimal("0"))),
                 "sold": str(sum((i.cash for i in data["items"]), Decimal("0"))),
                 "lots": sorted(i.lot_id for i in data["items"]),
                 "search_exhausted": data["exhausted"],
                 "residual": str(data["residual"])}
                for security, data in sorted(per_security.items())
            ],
            "buys": buys,
            "buys_blocked_by_wash_sale": blocked_buys,
            "certificate": (
                "Each overweight security was searched exhaustively for the subset of "
                "lots closing the most drift within the shared gain budget, never "
                "selling past target. Residual drift is a consequence of whole-lot "
                "trading, not of the search."
                if exhausted else
                "At least one security exceeded the exhaustive search limit; the "
                "selection there is greedy and no optimality is claimed."),
        }
        if drift_after > drift_before:
            # Should be unreachable: never selling past target means drift can
            # only fall. Reported rather than swallowed, because an engine that
            # made the portfolio worse must not return a success.
            diagnostics["infeasibility_reason"] = (
                f"Drift rose from {drift_before} to {drift_after}. This is a defect in "
                "the engine, not a property of the portfolio.")
            return Candidate(tuple(), "failed", diagnostics, self.name, self.version)

        if blocked_buys:
            diagnostics.setdefault("limitations", []).append(
                "Some purchases were withheld because the security was sold at a loss "
                "inside the wash-sale window. Buying back now would disallow that "
                "loss, so the drift in those securities stays open until the window "
                "closes. Re-run after that date, or rebalance into a different "
                "holding that is not substantially identical.")

        return Candidate(trades_for(problem, chosen), status, diagnostics,
                         self.name, self.version)

    # --- helpers ---------------------------------------------------------
    @staticmethod
    def _market_values(snapshot, prices) -> Tuple[Dict[str, Decimal], List[str]]:
        values: Dict[str, Decimal] = defaultdict(Decimal)
        unpriced: List[str] = []
        for lot in snapshot.lots:
            raw = prices.get(lot.security_id)
            if raw is None:
                if lot.security_id not in unpriced:
                    unpriced.append(lot.security_id)
                continue
            values[lot.security_id] += Money.of(raw).times(lot.quantity).quantized().amount
        return dict(values), unpriced

    @staticmethod
    def _drift(weights: Dict[str, Decimal], target, securities: Sequence[str]) -> Decimal:
        return sum((abs(weights.get(s, Decimal("0")) - target.weight_for(s))
                    for s in securities), Decimal("0"))

    def _select(self, by_security, excess, capacity, netting):
        """Choose lots per overweight security, sharing one gain budget.

        Securities are served in a fixed order and each takes only what the
        remaining budget allows, so the result is deterministic. Within a
        security the search is exhaustive: the subset closing the most drift
        without selling past target.
        """
        chosen: List[str] = []
        per_security: Dict[str, Dict[str, Any]] = {}
        exhausted = True
        remaining = capacity

        for security in sorted(by_security):
            lots = by_security[security]
            room = excess[security]
            if len(lots) > MAX_EXHAUSTIVE_LOTS:
                exhausted = False
            # Cash target is the overweight: sell up to it, never past it.
            outcome = exact_solver.solve(
                [i for i in lots if i.cash <= room] or [],
                remaining, netting, None, node_limit=self.node_limit)
            # Re-solve constrained to not exceed the room, by filtering any
            # selection that overshoots. The filter above removes single lots
            # bigger than the room; this trims combinations that together do.
            selected = self._trim_to_room(outcome.chosen, lots, room)
            picked = [i for i in lots if i.lot_id in selected]
            used = sum((i.gain for i in picked if i.gain > 0), Decimal("0")) \
                if netting is NettingBasis.GROSS_GAINS \
                else sum((i.gain for i in picked), Decimal("0"))
            remaining -= used
            sold = sum((i.cash for i in picked), Decimal("0"))
            per_security[security] = {
                "items": picked, "residual": room - sold,
                "exhausted": outcome.search_exhausted and len(lots) <= MAX_EXHAUSTIVE_LOTS,
            }
            if not per_security[security]["exhausted"]:
                exhausted = False
            chosen.extend(i.lot_id for i in picked)
        return sorted(chosen), per_security, exhausted

    @staticmethod
    def _trim_to_room(chosen: Sequence[str], lots, room: Decimal) -> List[str]:
        """Drop lots, largest first, until the selection fits inside the overweight."""
        by_id = {i.lot_id: i for i in lots}
        picked = sorted(chosen, key=lambda lid: (-by_id[lid].cash, lid))
        total = sum((by_id[lid].cash for lid in picked), Decimal("0"))
        kept: List[str] = []
        running = Decimal("0")
        for lot_id in picked:
            if running + by_id[lot_id].cash <= room:
                kept.append(lot_id)
                running += by_id[lot_id].cash
        return sorted(kept)

    @staticmethod
    def _recent_loss_securities(problem: OptimizationProblem) -> set:
        """Securities sold at a loss inside the wash-sale window.

        Buying one back restarts the clock and disallows the loss. The window
        comes from the rule pack rather than a constant, so a jurisdiction with
        a different period is a pack change and not a code change.
        """
        from datetime import timedelta
        from ..decisions.rule_registry import default_rules

        rules = default_rules(problem.as_of.year, as_of=problem.as_of)
        # The window reaches back as far as it reaches forward.
        window = timedelta(days=rules.wash_sale_window_days // 2)
        earliest = problem.as_of - window

        basis_by_security: Dict[str, Decimal] = {}
        for lot in problem.snapshot.lots:
            if lot.basis is not None and lot.quantity:
                basis_by_security.setdefault(
                    lot.security_id, lot.basis.amount / lot.quantity)

        at_a_loss = set()
        for txn in problem.snapshot.transactions:
            if txn.action is not Action.SELL or txn.trade_date < earliest:
                continue
            if txn.trade_date > problem.as_of or txn.amount is None or not txn.quantity:
                continue
            reference = basis_by_security.get(txn.security_id)
            proceeds_per_share = txn.amount.amount / txn.quantity
            # Without a basis reference the sale's character is unknown, and an
            # unknown is treated as a risk rather than waved through.
            if reference is None or proceeds_per_share < reference:
                at_a_loss.add(txn.security_id)
        return at_a_loss

    @staticmethod
    def _allocate(shortfall, proceeds: Decimal, prices,
                  washing: set) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Spread proceeds across underweight securities, pro rata to shortfall."""
        wanted = {s: v for s, v in shortfall.items() if v > 0}
        blocked = [{"security_id": s, "reason": codes.WASH_SALE_RISK,
                    "note": "This security was sold at a loss inside the wash-sale "
                            "window. Buying it back now would disallow that loss, so "
                            "the purchase is withheld and the drift stays open."}
                   for s in sorted(wanted) if s in washing]
        buyable = {s: v for s, v in wanted.items() if s not in washing}
        total_wanted = sum(buyable.values(), Decimal("0"))
        if total_wanted <= 0 or proceeds <= 0:
            return [], blocked
        spend = min(proceeds, total_wanted)
        buys = []
        for security in sorted(buyable):
            share = (buyable[security] / total_wanted) * spend
            if share <= 0 or security not in prices:
                continue
            buys.append({"security_id": security, "amount": share.quantize(Decimal("0.01")),
                         "price_per_share": str(prices[security])})
        return buys, blocked

    @staticmethod
    def _weights_after(values, total, per_security, buys, lot_security):
        after = dict(values)
        for security, data in per_security.items():
            sold = sum((i.cash for i in data["items"]), Decimal("0"))
            after[security] = after.get(security, Decimal("0")) - sold
        for buy in buys:
            after[buy["security_id"]] = after.get(buy["security_id"], Decimal("0")) \
                + buy["amount"]
        return {s: (v / total) for s, v in after.items()}
