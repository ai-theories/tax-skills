---
name: tax-profile
description: Establish the household scope, tax units, account classifications and stated assumptions that every other tax skill depends on. Use before the first analysis of an engagement, or when ownership, registration or residency is unclear.
---

# Tax profile

## When to use this

At the start of an engagement, and whenever scope is ambiguous.

## What to establish

1. **Operational household** — which accounts are managed together.
2. **Tax units** — which taxpayers those accounts belong to. A household can
   contain several: spouses filing jointly, an adult child, a non-grantor trust
   which is its own taxpayer with its own brackets and thresholds.
3. **Account registration** — taxable, IRA, Roth, qualified plan, trust. This
   drives wash-sale treatment.
4. **Entitlements** — which accounts this advisor may actually read. Household
   linkage does not authorize data sharing across RIAs.
5. **Stated assumptions** — any rate the client supplies is an assumption, not
   an observed fact, and is recorded as such.

## Sequence

`resolve_subject` returns the authorized accounts and tax units, and whether the
subject spans more than one tax unit. If it does, say so before any budget
conversation: a gain budget belongs to a taxpayer, not to a household.

## Stop and escalate when

- Residency or domicile is in question. A rule lookup cannot establish domicile.
- Trust classification is unclear. Grantor and non-grantor trusts are taxed
  differently and the trust instrument governs.
- An account's ownership is disputed.
