---
name: household-coordination
description: Coordinate harvesting and cash raising across a household's accounts or a UMA's sleeves under one shared gain budget, with cross-account and cross-sleeve wash-sale prevention. Use when a request spans more than one account or manager, or when a UMA overlay must stop one sleeve from washing another's loss.
---

# Household and UMA coordination

## When to use this

A request spans several accounts of one household, or several sleeves of a UMA.
Also use it whenever different managers may trade the same security for the same
taxpayer.

## Two engines, and the claim each one licenses

`optimize_household` solves the household as one problem. No ordering. Where
the search exhausts it reports `optimal` and a certificate, and you may say no
other selection of these lots does better within the budget.

`coordinate_household` runs each unit in a **declared order** against a shared,
decrementing budget, reserving as it goes. Use it when other runs or sleeves
are competing for the same allowance and the reservation matters. It is never
optimal.

- After the coordinator, say: "coordinated across the household's accounts
  under one $15,000 budget, screened for wash sales across all of them."
- After the coordinator, never say: "household-optimal", "the best allocation",
  or "optimized across the household." A different unit order can give a
  different answer, and the result reports `order_dependent: true` for exactly
  that reason.
- After the optimizer, check the status before claiming anything. A search
  stopped at its node limit reports `feasible` with a gap, and that gap is a
  ceiling from a relaxed problem rather than an improvement anyone can reach.

Even a proven optimum is optimal over the lots, prices and constraints supplied
and for one objective: reach the cash target, realize as little gain as
possible, use as few trades as possible. It is not a judgement about what the
household should do.

## The trade-off the optimizer will not make for you

Once the cash target is met the objective prefers fewer trades, so a loss lot
that costs nothing against the gain budget can be left unharvested. The run
reports these as `declined_loss_harvests`, with the loss foregone. Surface
them: the engine cannot weigh the client's transaction costs against the tax
benefit, and an advisor reading "optimal" will not expect an available harvest
to have been passed over.

## Sequence

1. `resolve_subject` — note `spans_multiple_tax_units`. If true, say so before
   any budget conversation: **a gain budget belongs to a taxpayer, not a
   household.** A non-grantor trust in the household has its own allowance.
2. Establish netting basis and period as for `harvest-review`. Ask; never default.
3. **Collect planned purchases from every manager** before running. A buy that
   is intended but not yet placed is invisible to transaction history, and that
   is precisely how one sleeve washes another sleeve's harvested loss.
4. `optimize_household` on `taxagent-household`, passing `planned_purchases`.
   Use `coordinate_household` instead only when budget reservation against
   other concurrent runs is what you need.

## UMA specifics

Sleeves are separate for allocation and **not separate for tax**. Every sleeve
in an account belongs to one taxpayer, so:

- A loss sold in sleeve A and repurchased in sleeve B is a wash sale. No
  internal netting avoids it; the overlay must prevent the pair.
- The run reports `cross_unit_conflicts_prevented`. Each entry names the selling
  unit, the buying unit and the security. Report these: a prevented harvest is a
  decision someone should see, not a silent omission.
- When a harvest is dropped to protect another sleeve's purchase, the selling
  manager's model drift is a real consequence. Name it rather than presenting
  the outcome as free.

Resolution options to offer, least disruptive first: defer the buy past the
61-day window; source the exposure from a different security; or drop the loss
sale and keep the buy.

## Reading the result

| Field | Meaning |
|---|---|
| `diagnostics.allocation_order` and `order_rule` | Which unit was served first, and why. Report it — it is the main lever the advisor can change. |
| `diagnostics.per_unit` | Trades, cash and budget consumed per account or sleeve, including units skipped and why. |
| `diagnostics.budget_consumed_by_tax_unit` | One entry per taxpayer, never one per household. |
| `diagnostics.cross_unit_conflicts_prevented` | Harvests withheld to protect another unit's purchase. |
| `diagnostics.optimality_gap` | What this allocation gave up. See below. |
| `BUDGET_RESERVED_ELSEWHERE` | Another run holds the capacity. Not infeasible — retry after it completes or is released. |

## Reporting the optimality gap

`provably_optimal: true` is a proof worth stating plainly: *"No other split of
this budget across the accounts raises more cash."*

A positive gap is a **ceiling**, not a forecast. Say: *"Serving the accounts in
a different order could have raised up to $20,000 against this budget rather
than $6,000. That upper figure assumes lots can be split and does not re-check
wash sales, so a real reallocation would capture some of it, not all."*

Never present the bound as an achievable alternative, and never present it as
an error. It is the measured cost of ordering, and the advisor's lever is the
unit order in `order_rule`.

## Stop and escalate when

- The user wants a joint optimum, or asks which order is best.
- The household spans tax units and the user wants one combined budget.
- A manager's planned purchases are unavailable; say the conflict check is
  partial rather than implying it is complete.

## Reference material

- `references/capability-boundaries.md` — what this deployment covers.
- `references/clarification-policy.md` — the household questions that must be
  asked, starting with whether the household is one tax unit or several.
- `references/glossary.md` — tax unit, sleeve, coordinated versus joint.
