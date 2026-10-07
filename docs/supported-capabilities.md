# Supported capabilities

`get_capabilities` is the authority. This document explains the boundary in
prose; where the two differ, the registry wins.

## Covered

| Dimension | Coverage |
|---|---|
| Scope | One account, or a coordinated household / UMA (`household_coordinated`) |
| Asset classes | US equities, US-listed ETFs, long positions |
| Currency | USD only |
| Jurisdiction | US federal |
| Tax year | 2026 |
| Mode | Read-only analysis |

Calculations: lot-level gain and loss with holding-period character; wash-sale
screening by exact security identifier with coverage reporting; rule-based
loss-harvest candidate selection under a cash target and gain budget;
exact optimization at account and household scope; tax-aware rebalancing to
advisor-supplied target weights; independent validation; evidence packaging.

**Optimization, and what each engine claims.** Three engines prove optimality
and two deliberately do not. `oracle-account-adapter` and `household-optimizer`
report `optimal` only when their search provably exhausted the space, and
degrade to `feasible` with a reported gap otherwise; `rebalance-engine` is
exact per overweight security. The greedy selector and the sequential
coordinator never claim it. Optimal means optimal over the lots, prices and
constraints supplied, for one stated objective — not a judgement about what the
account should do.

## Not covered

**Tax liability.** No return-level engine is bound. The system produces gains
and losses. Converting those to tax owed needs brackets, other income, the NIIT
threshold, capital-loss carryforwards, AMT and state treatment — and a
validated engine.

**Substantial identity.** Matching is by exact identifier. Whether two
different securities are substantially identical is a reviewed policy question
the screener explicitly declines to answer.

**Asset classes beyond equities and ETFs.** Municipal bonds, options,
partnerships issuing K-1s, digital assets, equity compensation, and foreign
holdings each need their own rules, data and fixtures.

**Basis determination beyond simple purchases.** Gift, inheritance, and
partnership adjustments need specialist evidence.

**Execution.** No tool stages, places or approves a trade. A future execution
service would need action-bound authorization enforced by the receiving system.

## Known limits of what *is* covered

- The harvest selector is greedy. It reports `feasible`, never `optimal`.
- Coordination is sequential, and is one of two household engines. A different
  unit order can change its result, so the order, its rule, and the optimality
  gap against an LP bound are recorded in every coordinated evidence package. A
  positive gap is a ceiling on what a joint optimizer could add, never a
  forecast of what it would achieve. `household-optimizer` has no ordering and
  proves optimality where its search exhausts; prefer it unless budget
  reservation against concurrent runs is what you need.
- Above 40 budget-consuming lots in a tax unit the search is not exhausted, the
  status degrades to `feasible`, and a gap is reported. Measured capture of the
  relaxed bound at that size stays above 99 percent, so the limit is the proof
  rather than the quality of the plan.
- Rebalancing never sells a security below its target, so with chunky lots it
  under-trades rather than overshooting and reports the residual drift. A
  purchase is withheld when the security was sold at a loss inside the
  wash-sale window.
- Cross-unit wash-sale prevention sees accounts in scope plus the planned
  purchases supplied with the request. Manager intentions not supplied, and
  accounts not readable, remain outside it.
- A wash-sale screen is bounded by transaction coverage. It cannot clear an
  account it has not read, including a spouse's account at another custodian.
- The forward 30-day window stays open after a run; any screen covering it is
  provisional until the date given.
- A snapshot makes analysis reproducible. It does not prove a scenario is still
  executable after prices, balances, open orders or corporate actions move.
