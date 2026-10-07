---
name: penalty-review
description: Compute late filing and late payment additions to tax, and partnership or S corporation late-filing penalties. Use when a return or payment is late. Interest is never included, so the figure is always lower than what is owed.
---

# Penalty review

## When to use this

A return or a payment is late and someone wants to know the cost, or an entity
missed its filing date.

## Call

`calculate_filing_penalty` on `taxagent-compliance` for an individual or
corporate income tax return; `calculate_entity_penalty` for a partnership or S
corporation.

## State this every time

**Interest is not included.** The underpayment rate is set quarterly and
compounds daily, so no rate is stored in this deployment and the real amount
owed is higher than the figure you report. Never present a penalty total as
the cost of being late.

## The most useful thing you can tell someone

Filing is ten times more expensive than paying: 5% a month against 0.5%. A
client who cannot pay should still file, and often should file an extension
even with nothing to send. Lead with this when a return is not yet in.

Other points worth making unprompted:

- Any part of a month counts as a whole month, so one day late already costs a
  full month of both additions.
- Where both additions run in the same month, failure-to-file is reduced by
  failure-to-pay. The tool applies this; do not add it again.
- Reasonable cause and first-time abatement are not applied by the tool and can
  remove the penalty entirely. If the facts suggest either, say the computed
  figure may not be what is owed.

## The entity penalty surprises people

It is charged per owner per month and does not depend on tax owed. A
partnership with no taxable income and eight partners owes thousands for a few
months' delay. Say so before giving the number, not after.
