---
name: entity-review
description: Compute section 179 expensing with both of its limits, and compare federal tax on the same income inside a C corporation against passing it through. Produces arithmetic over supplied rates, never an entity-choice recommendation.
---

# Entity review

## When to use this

A business is deciding how much equipment cost to expense this year, or an
owner wants to see the federal tax difference between a C corporation and a
pass-through.

## Section 179

Call `calculate_section_179` on `taxagent-business`. Supply the total cost
placed in service, and business taxable income if the second limit should
apply.

Report **which limit bound**, because the responses differ:

- The **dollar limit** falls one-for-one with spending above the threshold.
  That is a timing problem with a timing answer: deferring a purchase across
  the year end can restore the limit.
- The **business income limit** caps the deduction at income, and the excess
  carries forward. Nothing is lost, so there is nothing to fix.

## The entity comparison

Call `calculate_entity_comparison` on `taxagent-business`. Before you do, ask
two things rather than defaulting:

1. The owner's actual marginal rate, not the top one.
2. What fraction of profit is really distributed. Defaulting to all of it
   quietly favours the pass-through, because the second corporate layer is
   only paid when money comes out.

## What you must say about the result

It is federal income tax over the rates supplied, and it excludes
self-employment tax, the net investment income tax, payroll on reasonable
compensation, and state and local tax. Any of those can reverse it, and
reasonable compensation is usually the larger number in an S corporation.

If asked "which should I choose", give the arithmetic and then say the
decision turns on exit plans, qualified small business stock, benefits,
ownership restrictions and the cost of converting later — none of which is
arithmetic. Do not pick one. Converting away from a C corporation is costly
and sometimes effectively one-way, so a close result is not a reason to move.
