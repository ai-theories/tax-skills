# Capability boundaries

Call `get_capabilities` before promising anything. The registry is the only
authority on what this deployment covers; this file explains how to read it.

## What V1 covers

- US equities and US-listed ETFs, long positions, USD, one account at a time.
- Lot-level gain and loss from a frozen snapshot.
- Wash-sale screening by exact security identifier, with explicit coverage gaps.
- Deterministic loss-harvest candidates under a cash target and a gain budget.

## What V1 does not cover, and must never be implied

| Not covered | What to say instead |
|---|---|
| Household-level optimization | "No validated household optimizer is available. I can analyze each account separately, and per-account results are not household-optimal." |
| Tax liability in dollars | "I can show realized gains and losses. Converting those to tax owed needs a validated liability engine, which is not connected." |
| Whether two different securities are substantially identical | "That is a reviewed policy question. The screen matched exact identifiers only." |
| Municipal bonds, options, K-1 partnerships, crypto, equity comp | "That asset class is outside this release's coverage." |
| Any statement that a portfolio is "clear" of wash sales | "The screen is complete for the accounts and dates listed; it cannot clear accounts it has not seen." |

## Vocabulary that must stay distinct

- **Realized gain budget** is a limit on gains. A **tax dollar budget** is a limit
  on tax. They are different constraints and one is never substituted for the other.
- **Gross gains** exclude losses. **Net gains** offset them. The answer changes;
  always confirm which the client means.
- **Harvested loss**, **estimated current tax reduction**, and **lifetime after-tax
  benefit** are three different numbers. Only the first is computed here.
- A **household** is an operational grouping. A **tax unit** is a taxpayer. A
  household can contain several.
