---
name: estate-review
description: Estimate federal estate tax against the unified credit, including the effect of lifetime gifts and a deceased spouse's unused exclusion. Use when someone asks what an estate would owe. Never values property and never decides what is in the gross estate.
---

# Estate review

## When to use this

Someone wants to know what federal estate tax an estate would owe, or how
lifetime gifts and portability change it.

Do not use for: what property is worth, what belongs in the gross estate,
generation-skipping transfer tax, or any state estate or inheritance tax.

## Before asking the backend

1. **Gross estate.** Everything includible at date-of-death value. Say that
   this figure is the user's, not something you verified.
2. **Deductions.** Marital, charitable, debts and administration expenses.
3. **Lifetime taxable gifts.** Gifts above the annual exclusion in prior
   years. If unknown, say the result is provisional — passing zero is a claim
   that none were made.
4. **Deceased spouse's unused exclusion**, if any.

## Call

`calculate_estate_tax` on `taxagent-estate`.

## Reading the result

Lead with the tax and the exclusion available. Then explain, in this order:

- Lifetime gifts were **added** to the taxable estate, not subtracted. They
  push the estate into higher brackets. People consistently expect the
  opposite, so say it explicitly whenever gifts are non-zero.
- The credit shown is tax on the exclusion, not the exclusion itself.

Never say "no estate tax is due" without naming the tax you computed. Several
states impose estate or inheritance tax at thresholds far below the federal
exclusion, and none of them is computed here.

Do not describe the current exclusion as permanent, and do not predict what it
will be. Report it for the year you computed.
