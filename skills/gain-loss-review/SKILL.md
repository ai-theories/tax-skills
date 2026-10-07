---
name: gain-loss-review
description: Calculate realized and unrealized gain and loss at lot level, split by holding period, from a frozen snapshot. Use when the user asks what they would realize by selling, where they stand on gains this year, or how a sale splits between short and long term.
---

# Gain and loss review

## When to use this

The user asks what a sale would realize, how much gain they have taken this
period, or how a disposal splits between short-term and long-term.

## What the numbers mean

- **Character** follows the holding period: long-term requires **more than one
  year**, so a sale on the one-year anniversary is short-term.
- **Gross gains** exclude losses. **Net gain** offsets them. Ask which the user
  means before answering a question about "gains".
- Amounts are gains and losses, **not tax**. No liability engine is connected;
  converting to tax owed requires facts and an engine this release does not have.

## Sequence

1. `resolve_subject`.
2. `review_lots` on `taxagent-portfolio`.
3. Read `results.gain_loss.totals` and the per-lot dispositions.

## Reading the result

- `blocked_count` above zero means some lots could not be calculated. Name them
  and say why; the totals exclude them.
- `short_term_gain` and `long_term_gain` are reported separately because they
  are taxed differently. Do not merge them into one figure.

## Stop and escalate when

- The user asks for the tax due on a gain.
- The user asks about a loss carryforward. That is a return-level fact this
  release does not hold; ask for it or route to the CPA.
- Lots are blocked and the answer would be misleading without them.
