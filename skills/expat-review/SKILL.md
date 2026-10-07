---
name: expat-review
description: Compute the foreign earned income exclusion and housing amount for a US taxpayer working abroad, and check whether a gift from a foreign person must be reported. Never establishes eligibility and never compares against the foreign tax credit.
---

# Expat review

## When to use this

A US taxpayer is living or earning abroad and wants to know what can be
excluded, or has received money from someone overseas and wants to know what
to report.

## Before asking the backend

1. **Foreign earned income.** Earned, not investment income — the exclusion
   covers compensation for services performed abroad.
2. **Qualifying days.** Which test, and how many days. Ask rather than
   assuming a full year.
3. **Housing expenses**, if any.

## Call

`calculate_feie` on `taxagent-international`, or `calculate_foreign_gift` for
a receipt from abroad.

## The thing you must not let pass

The result assumes the taxpayer qualifies. It does not establish it. Say so
every time, and name the test they would need to meet: a tax home abroad plus
either bona fide residence for a full tax year or physical presence for 330
full days in twelve months.

## The choice this skill cannot make

Excluded income cannot also generate a foreign tax credit, and in a high-tax
country the credit is often worth more. This deployment does not compute the
credit. If the user is deciding whether to claim the exclusion, say that the
comparison needs the credit, that it is outside what you can compute, and that
revoking the election later bars re-electing for five years without consent.

Answering "here is your exclusion" to someone asking "should I take it" is the
failure mode here.

## Reporting is not tax

`calculate_foreign_gift` answers a disclosure question. A gift from abroad is
generally not income. Aggregate gifts from related foreign donors across the
year, and note that foreign entities have a much lower threshold than
individuals.
