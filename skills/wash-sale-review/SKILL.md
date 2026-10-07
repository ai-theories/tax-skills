---
name: wash-sale-review
description: Screen proposed or completed loss sales for wash sales under IRC section 1091, across the accounts available, with explicit coverage gaps. Use when the user asks whether a loss is allowed, about the 30-day rule, or about repurchasing a security they sold at a loss.
---

# Wash-sale review

## When to use this

The user asks whether a loss will be disallowed, mentions buying back something
they sold at a loss, or asks about the 30-day rule.

## What the screen does and does not establish

The window is 30 days before through 30 days after the sale. Matching is by
**exact security identifier**. Replacement purchases are matched across every
account in scope, including IRAs.

The screen returns `screened_complete` only when transaction coverage spans the
whole window for every in-scope account and the forward window has closed.
Otherwise it returns `screened_with_gaps`. **Never** describe either result as
"clear" or "clean".

## Sequence

1. `resolve_subject` — identify the account and its tax unit, so related
   accounts can be named even when they cannot be read.
2. `review_lots` on `taxagent-portfolio`.
3. Read `results.wash_sale` in the evidence package.

## Reading the result

- `matched_quantity` / `unmatched_quantity` — a replacement smaller than the
  sale disallows only the matched share of the loss. Report both.
- `treatment: PERMANENT_DISALLOWANCE` — the replacement was bought in a
  retirement account. The loss is **gone**, not deferred: no basis adjustment is
  available (Rev. Rul. 2008-5). This is materially worse than an ordinary wash
  sale and must be stated plainly.
- `treatment: BASIS_ADJUSTMENT` — the disallowed loss moves into the
  replacement lot's basis and its holding period tacks.
- `COVERAGE_GAP` / `UNKNOWN_RELATED_ACCOUNTS` — say which accounts were not
  screened. A spouse's account or an outside custodian can create a wash sale
  this screen cannot see.
- `FUTURE_WINDOW_OPEN` — the answer is provisional until the date given.
- `EQUIVALENCE_NOT_ASSESSED` — always present. Different tickers were not
  compared for substantial identity. See
  `references/coverage-and-equivalence.md` for what the screen establishes,
  what it refuses to decide, and the wording to use.

## Stop and escalate when

`references/coverage-and-equivalence.md` lists the full set. In short:

- The user wants a ruling on whether two different securities are substantially
  identical.
- Coverage is missing for an account that plausibly holds the same security.
- The user asks you to confirm a loss is safe to claim on a return.
