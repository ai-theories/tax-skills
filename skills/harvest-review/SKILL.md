---
name: harvest-review
description: Review tax-loss harvesting opportunities in one taxable account, with wash-sale screening and an explicit gain budget. Use when the user asks about harvesting losses, offsetting gains, or raising cash tax-efficiently. Produces scenarios for professional review, never a trade instruction.
---

# Harvest review

## When to use this

The user wants to know what losses could be harvested, or wants to raise cash
while keeping realized gains within a limit, in a taxable account.

Do not use for: a household-wide request (no validated household optimizer
exists — say so), retirement accounts (no taxable gain to harvest), or asset
classes outside `get_capabilities`.

## Before asking the backend

Confirm these, because each one changes the answer:

1. **Which account.** One taxable account. If the user named a household,
   explain that account-level results are not household-optimal and ask which
   account to analyze.
2. **Gain budget netting basis** — gross or net. Losses offset gains under net
   netting and do not under gross netting.
3. **Budget period**, and whether gains already realized this period consume it.
4. **Cash target and the withdrawal account**, if raising cash.

If any is missing, ask. Do not pick a default. `review_harvest` returns
`needs_input` with the exact field names if you skip this.

## Sequence

1. `resolve_subject` — confirm the account and its tax unit.
2. `review_harvest` on `taxagent-portfolio`. The backend freezes a
   snapshot, validates lots, compiles constraints, generates candidates,
   recomputes gains, screens wash sales, validates independently, and stores
   evidence.
3. `get_scenario` if you need the full evidence package.

You do not compute gains, losses, holding periods or wash sales yourself. If a
number is not in the result, you do not have it.

## Reading the result

| Field | What it means |
|---|---|
| `status: completed_with_limitations` | The calculation finished **and** something about real-world coverage is incomplete. Report both. |
| `status: blocked`, `code: INFEASIBLE_CONSTRAINTS` | The request cannot be met. Report the shortfall and ask which constraint to change. Never retry with a quietly relaxed constraint. |
| `validation.external_account_coverage: incomplete` | The wash-sale screen did not see every relevant account. |
| `limitations[].code: FUTURE_WINDOW_OPEN` | A purchase in the next 30 days could still create a wash sale. Say the screen must be repeated. |
| `limitations[].code: MISSING_BASIS` | Some lots were excluded because basis is unknown. Name them. |
| `execution_authorized: false` | Always. Nothing here authorizes a trade. |

## Stop and escalate when

- The user asks what tax they will owe. No liability engine is connected.
- The user asks whether a replacement ETF is "safe". That is a reviewed policy
  question; the screen matched exact identifiers only.
- Basis is missing on the lots that matter.
- The user asks you to place, stage, or approve a trade.

## Output

State the cash raised, losses harvested, realized gains against the budget, and
every limitation returned. Attribute each number to the result. Close with a
note that the scenario is prepared for review by the responsible professional.

## Reference material

- `references/capability-boundaries.md` — how to read the registry before
  promising anything.
- `references/clarification-policy.md` — when a missing decision must be asked
  rather than defaulted. A gain budget's netting basis is the standing example.
- `references/glossary.md` — basis, covered security, netting basis, tax unit.
