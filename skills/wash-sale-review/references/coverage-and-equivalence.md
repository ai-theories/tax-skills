# Coverage and equivalence

Two separate questions decide whether a wash-sale answer means anything. The
screener answers one of them and deliberately refuses the other.

| Question | Who answers it |
|---|---|
| **Coverage** — did we see the transactions that would reveal a wash sale? | The screener, from declared coverage intervals. Reported as `screened_complete` or `screened_with_gaps` |
| **Equivalence** — are two different securities substantially identical? | Nobody here. Reported as `EQUIVALENCE_NOT_ASSESSED` and escalated |

## 1. Coverage

The screener matches replacement purchases across every in-scope account in the
**tax unit**, including retirement accounts, over the 61-day window, and it
includes purchases a manager has *planned* but not yet placed.

`screened_complete` requires all of:

- A declared coverage interval spanning the whole window for every in-scope account.
- No related account in the tax unit left unread (`UNKNOWN_RELATED_ACCOUNTS`).
- The forward half of the window already closed (`FUTURE_WINDOW_OPEN`).

If any fails, the result is `screened_with_gaps`. **There is no third state.** A
screen is never "clear", and the word should not appear in anything you write.
The common real-world gaps:

- A spouse's account at another custodian. Same tax unit, invisible to us.
- An account this advisor is not entitled to read.
- The 30 days after the sale, which have not happened yet.
- Dividend reinvestment scheduled inside the window.

## 2. Equivalence

Matching is by **exact security identifier**. The rule pack records this as
`replacement_matching_basis: exact_security_identifier` — the system's position
is that it does not decide substantial identity.

Section 1091 says "substantially identical stock or securities" and gives no
bright-line test. The IRS has not issued comprehensive guidance for funds and
ETFs. What follows is the shape of the question a reviewer answers, **not a
test you may apply yourself**:

| Situation | Commonly treated as | Confidence |
|---|---|---|
| Same issuer, same class | Identical | Settled |
| Different share classes of the same fund | Substantially identical | Strong |
| An option or contract to acquire the same stock | Within section 1091(a), which names contracts and options | Settled |
| Two ETFs tracking the **same** index | Substantially identical by most firm policies | Policy, not law |
| Two ETFs in the same asset class tracking **different** indexes | Not substantially identical | Policy, and contested |
| A stock and its successor after a merger or reorganisation | Depends on the corporate action | Specialist |
| Bonds differing in issuer, coupon or maturity | Not identical | Generally accepted |

Two things that are **not** arguments: a different ticker, and a high
correlation. Neither settles the question in either direction, and saying so is
the most common way this analysis goes wrong.

## 3. What this means for your output

Do say:

> "The screen matched exact identifiers across the accounts listed. Whether the
> replacement you are considering is substantially identical to what you sold is
> a judgement your tax adviser must make; I have not assessed it."

Never say a replacement is "safe", "different enough", or "not substantially
identical", and never infer it from correlation, ticker, issuer or category.

## 4. Escalate when

- The client asks whether a specific replacement is acceptable.
- The replacement is a different share class, or an ETF tracking the same index.
- A corporate action sits inside the window.
- Coverage is missing for an account that plausibly holds the same security.
- The loss is large enough that being wrong is material.

## 5. For the firm

Adopting a house equivalence policy is the way to make this deterministic. It
belongs in a versioned rule pack under `rules/tax/`, with an authority, an
effective date and a named reviewer — the same lifecycle as every other tax
rule. Until one exists, the screener's refusal is the correct behaviour, and a
policy table pasted into a prompt is not a substitute for one.
