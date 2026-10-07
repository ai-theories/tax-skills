---
name: rebalance-review
description: Rebalance one account toward advisor-supplied target weights within a gain budget, showing the tax cost and the drift that remains. Use when the user asks what rebalancing would cost, or wants to compare acting now against leaving the drift.
---

# Rebalance review

## When to use this

The user has approved targets and wants to move toward them, or wants to
compare acting now against leaving the drift.

Targets come from the advisor. This skill does not propose an allocation: an
engine that inferred a target from the current holdings would be measuring
drift against its own assumption.

## Sequence

1. `resolve_subject` on `taxagent-portfolio`, with the document.
2. Establish the gain budget the way `harvest-review` does: netting basis,
   period, prior realized gains. Ask; do not default.
3. `review_rebalance` on `taxagent-portfolio`, with `target_weights` and
   optionally a `tolerance`.
4. Present no-trade as the baseline alongside whatever the run proposes.

## Never substitute the harvest tool

`review_harvest` selects for losses, not for target weights. It answers a
different question, and the evidence package would record a harvest run. If
the rebalance tool refuses, report the refusal.

## Reading the result

Lead with drift before and after, and the tax it cost to close that much.

Then the two things the engine does that a reader will not expect:

**It under-trades rather than overshooting.** No security is sold below its
target, so when lots are chunky some drift stays open. `per_security` carries
the residual for each one. Say the portfolio is closer to target, not that it
is rebalanced.

**It withholds a purchase that would wash a loss.** If the account sold a
security at a loss inside the wash-sale window, buying it back now disallows
that loss — and the client would see neither the loss nor a warning. The run
reports which securities were withheld. Tell the user which, why, and that
re-running after the window closes will finish the job, or that a
not-substantially-identical holding is the other way through.

## What it does not weigh

Transaction costs, market impact, tracking error, and which account should
hold which exposure. A result that is optimal for drift within a gain budget
is not automatically the right trade.

## Reference material

- `references/capability-boundaries.md` — asset classes and scopes covered.
- `references/clarification-policy.md` — targets come from the advisor and are
  never inferred; this is why.
