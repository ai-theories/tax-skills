# Joint household optimization: what it would take

> **Status, 2026-10-06: built.** `household-optimizer` is `implemented` in
> `capabilities/engines.yaml` with `claims_optimality: true`, and the
> `household` scope is served. The gate below was cleared by a different route
> than this document proposed: an exact branch-and-bound solver decomposed by
> tax unit, rather than a MILP. The decomposition is exact because a gain
> budget belongs to one taxpayer, so no selection in one unit changes what is
> available in another. See `src/taxagent/optimization/household_optimizer.py`
> and `tests/test_engines.py`.
>
> What is kept below: the analysis of what sequential coordination cannot do,
> which is still the reason the joint engine exists, and the gap measurement
> that made the decision empirical. What is now historical: the six conditions
> in §6 and the build order in §7.

`capabilities/engines.yaml` lists `household-optimizer` as `unavailable`, and
the `household` scope stays refused. This document is the gate: what has to be
true before that entry changes, and what the honest intermediate step is.

## 1. What the coordinator does not do

`SequentialHouseholdCoordinator` walks units in a declared order against a
decrementing budget. Three things a joint optimum can do that it cannot:

**Look ahead.** Sequential spends the shared budget on whoever is served first.
A joint model can decline a mediocre harvest in sleeve A so sleeve B can take a
better one.

**Substitute across units.** It can realize a gain in one account to unlock a
larger loss elsewhere, or choose which account sells a security both hold.

**Trade off coupled constraints simultaneously.** Cash target, gain budget,
concentration limits and tracking error span units. Sequential resolves them
greedily in order; that order is recorded in `diagnostics.order_rule` precisely
because it changes the answer.

The gap is real but bounded. For a single shared budget and no risk model, the
sequential result is often close. It is never *provably* close, which is why
nothing in the output says "optimal".

## 2. Measure the gap before building anything

Every coordinated run now publishes `diagnostics.optimality_gap`: an upper
bound on the cash any allocation of the same budget could raise, against what
sequential actually raised.

The scarce resource is the gain budget and the objective is cash, so relaxing
lots to be divisible makes it a fractional knapsack, which greedy solves
exactly — no solver dependency. Losses are taken first (they raise cash and,
under net netting, return capacity); the remainder is filled with gain lots
ranked by cash per dollar of budget consumed. The bound is computed **per tax
unit** and summed, and capped at the cash target, since no allocation pursues
cash nobody asked for.

Reading it:

- `provably_optimal: true` — a proof. No reallocation of this budget raises
  more cash. Sequential was optimal for this instance and a joint optimizer
  would add nothing.
- `gap: "14000.00", gap_percent: "70.00%"` — a **ceiling** on the prize, not a
  forecast. The bound ignores whole-lot trading, restriction interactions,
  re-screening wash sales against a different trade set, and round lots.

This turns "is joint optimization worth building" into an empirical question.
Run it across the real book for a quarter. If gaps cluster near zero, the MILP
buys nothing and §3 onwards is wasted work. If they are consistently large,
the shadow-price step below is justified and the numbers say by how much.

## 3. The intermediate step: price the shared budget

Before a monolithic model, there is a cheaper move that captures most of the
coupling: treat the gain budget as a **priced resource** rather than a
first-come allocation.

Sequential allocation is Lagrangian decomposition with the budget's shadow
price fixed at zero. Instead:

1. Start with a price `λ` on budget consumption.
2. Solve each unit independently against an objective penalised by `λ` per
   dollar of budget consumed. Units are solved in parallel, not in order.
3. Sum the consumption. Over budget, raise `λ`; under, lower it. Bisect.
4. Stop on convergence or an iteration cap; the final `λ` is the budget's
   marginal value, which is itself useful to report to an advisor.

This removes order dependence, keeps per-unit subproblems small, reuses the
existing account selector behind `OptimizerPort`, and can be benchmarked
against the sequential baseline on the same fixtures. It still does not prove
optimality — integrality and non-convexity leave a duality gap — so it would
register as a better `household_coordinated` engine, not as `household`.

Estimated effort: 1–2 weeks including tests and the benchmark harness.

## 4. The joint formulation

A MILP over all lots in the household.

**Variables.** `x_l ≥ 0`, shares sold from lot `l`, bounded by `q_l`. A binary
`y_l` where specific identification or a minimum trade size matters.

**Objective.** Minimise `tax_cost + μ·transaction_cost + λ·tracking_error`.
Units differ, so coefficients must be normalised and **disclosed**: a weighted
objective silently encodes a client preference otherwise.

**Constraints.**
- Cash: `Σ x_l · p_l ≥ target`, sourced from the withdrawal account.
- Gain budget, **per tax unit**: `Σ_{l ∈ T} x_l · (p_l − b_l) ≤ B_T`.
- Restrictions, concentration bounds, per-sleeve model weights.
- Wash sales: the hard part, below.

**Wash sales are the coupling that makes this a MILP, not an LP.** Selling lot
`l` at a loss is only worthwhile if no purchase of the same security happens in
any account of that tax unit within 61 days. With buys as decision variables
too, that is a logical implication:

```
sell_at_loss(l)  →  Σ buys of security(l) across the tax unit, in window = 0
```

which needs indicator constraints or a big-M pair, plus binaries linking sells
and buys per security. Scale follows: a household with 10 accounts and 2,000
lots each is ~20k binaries before wash-sale linkage. That is where decomposition
(per-security subproblems, or column generation on the shared budget) stops
being optional.

## 5. What it drags in

**Non-convex tax.** A *gain* budget is linear and fine. A *tax dollar* budget is
not: brackets, the NIIT threshold, capital-loss limits, AMT crossover and
phase-outs are piecewise. Approximate with a piecewise-linear marginal rate
schedule (SOS2 or binaries) and accept the approximation error, or iterate
against the real engine.

**The convergence loop.** With a liability engine bound, the optimizer minimises
a proxy while the engine computes the truth. Naive iteration oscillates. Before
building, specify: what schedule the optimizer receives, the iteration cap, the
convergence tolerance, whether rate updates are forced monotone, and the
deterministic fallback when it does not converge. This is item 1 in
[open-questions.md](open-questions.md) and it is the single most likely cause of
a system that works in testing and thrashes in production.

**Determinism.** MIP solvers vary with thread count and time limit, and several
do not guarantee bit-identical output even pinned. Requirements: deterministic
mode, fixed seed, fixed thread count, fixed time limit, a declared tie-breaking
rule, and all of it in `EngineManifest.solver_settings`. Replay should re-verify
a **stored** solution rather than re-solve, because re-solving is the part that
cannot be guaranteed.

**Rounding.** Solvers work in floats and produce fractional shares. Round to
whole shares and cents, then **revalidate**: a rounded solution can violate a
constraint the solver satisfied. The repair step is part of the engine, not an
afterthought, and `validation/` already recomputes independently.

**Infeasibility diagnosis.** We promise to return infeasibility rather than
relax constraints. At household scale "infeasible" is useless without saying
which constraints conflict, so the adapter must extract an irreducible
infeasible set and map it back to client-meaningful terms.

## 6. Gate: what must pass before `scope: household` is registered

1. Reference tests including a small instance with a **known optimum**, solved
   to proven optimality with the gap reported.
2. A determinism harness: identical trades across 20 runs and across at least
   two thread counts, with the manifest pinned.
3. Rounding repair verified — a fractional solution that would breach a
   constraint is caught and corrected.
4. Infeasibility returns an IIS mapped to named constraints.
5. A benchmark against the sequential coordinator on shared fixtures showing
   the improvement is real and quantified — and that it closes a materially
   large share of the measured gap from §2, not a rounding error.
6. A named owner for the model and the objective coefficients.

Until all six hold, `household` stays `unavailable` and the coordinator keeps
saying "coordinated", not "optimal".

## 7. Build order

| Step | Outcome | Effort |
|---|---|---|
| Gap measurement (§2) | The decision below becomes empirical | **done** |
| Shadow-price coordination (§2) | Order dependence removed; budget's marginal value reported | 1–2 weeks |
| Single-account MILP behind `OptimizerPort` | Proven optimality at account scope; determinism harness built once | 3–4 weeks |
| Household MILP without a liability engine | Joint over a *gain* budget, which stays linear | 4–6 weeks |
| Liability engine + convergence loop | Tax-dollar budgets | Gated on §4 |
