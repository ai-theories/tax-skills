---
name: lot-review
description: Check tax-lot completeness and integrity before any tax calculation — basis, acquisition dates, quantities, duplicates and position reconciliation. Use when lots have just been imported, a transfer arrived, or another skill reported blocked calculations.
---

# Lot review

## When to use this

Before relying on any gain, loss or harvest number, and whenever a result came
back with `MISSING_BASIS`, `POSITION_MISMATCH` or `DUPLICATE_LOT`.

## Sequence

1. `resolve_subject`.
2. `review_lots` on `taxagent-portfolio`.
3. Read `results.gain_loss.blocked_count` and the snapshot findings.

## What the findings mean

| Code | Severity | Consequence |
|---|---|---|
| `MISSING_BASIS` | blocking | Gain and loss for that lot cannot be computed. It is excluded, not zeroed. |
| `MISSING_ACQUISITION_DATE` | blocking | Holding period is undeterminable, so character is unknown. |
| `DUPLICATE_LOT` | blocking | Holdings would be double counted. |
| `POSITION_MISMATCH` | blocking | Lot quantities do not sum to the reported position; the ledger disagrees with itself. |
| `BASIS_PROVENANCE_UNVERIFIED` | limiting | Basis came from a transfer or the client, not a covered custodian record. Confirm before relying on it. |
| `COVERAGE_UNKNOWN` | limiting | No transaction history window declared; wash-sale screening cannot be complete. |

## Stop and escalate when

- Basis is missing on a material position. Ask for the custodian's transfer
  statement or the original confirmation; do not estimate it.
- A position mismatch persists after re-import. That is a reconciliation
  exception for the operations team, not something to explain away.

## Output

List the exceptions, name the specific lots, and state which downstream
calculations are blocked by each. Do not proceed to harvest or gain analysis on
a blocked snapshot without saying what is excluded.
