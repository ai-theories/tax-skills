---
name: muni-review
description: Compare a municipal bond against a taxable alternative, and work out how a discount or premium will be taxed. Use for muni yield questions, de minimis market discount, and bond premium. Does not decide whether a bond is a private activity bond and does not look up any state's treatment.
---

# Municipal bond review

## When to use this

Someone is comparing a muni against a taxable bond, or holds one bought above
or below par and wants to know how it will be taxed.

## Before asking the backend

Four facts change the answer, and three of them are routinely assumed:

1. **Federal marginal rate** — theirs, not the top one.
2. **State rate, and whether the bond is exempt in their state.** An
   out-of-state bond is federally exempt and state taxable.
3. **Whether the 3.8% surtax applies.** Exempt interest is not net investment
   income, so it is part of what the muni avoids.
4. **Whether it is a private activity bond.** If it is, and the holder is in
   AMT, the interest is a preference item and the advantage is gone.

Ask for all four. If the private activity status is unknown, say so and give
the answer conditionally rather than assuming it is an ordinary bond.

## Call

- `calculate_muni_yield` on `taxagent-muni` for the comparison.
- `calculate_muni_discount` for a bond bought below par.
- `calculate_muni_premium` for one bought above par.

## Reading the result

Lead with the taxable-equivalent yield and what the taxable bond would have to
pay. Then the two figures that show why the naive answer was wrong: the uplift
from state tax and the uplift from the surtax.

For a discount bond, report the threshold next to the discount. Above it, the
accretion is ordinary income on disposition — say this plainly, because a
client who bought a muni for the exemption will not expect the top rate.

For a premium bond, say there is no loss at maturity before they ask for one.

## What this will not do

No state's treatment is looked up. If the user offers their state's rules, use
their rate as an input but do not confirm or correct the rule itself.

Municipal bonds are outside every portfolio engine in this deployment, so
there is no harvesting, wash-sale screening or rebalancing for a muni holding.
Say so rather than running an equity tool over it.

A yield comparison is not a recommendation. Credit, calls, duration and
liquidity are absent from it and frequently decide the question.

## Reference material

- `references/capability-boundaries.md` — what this deployment covers.
- `references/clarification-policy.md` — why the four facts above are asked
  rather than defaulted.
