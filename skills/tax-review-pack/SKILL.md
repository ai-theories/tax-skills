---
name: tax-review-pack
description: Assemble a source-bound review package for a completed analysis — scope, assumptions, results, validation, limitations and version pins — for the responsible professional. Use when the user wants something to review, file, sign off, or hand to a CPA.
---

# Tax review pack

## When to use this

An analysis has completed and a person needs to review it, or a CPA needs a
handoff.

## Sequence

1. `get_scenario` with the `evidence_ref` from the run.
2. Render using `templates/review-report.md`.

## The rule that governs this skill

Every number in the pack must come from the evidence package. The package's
`claim_map` gives the field behind each claim. If a number is not in the
package, it does not go in the pack — do not recompute, round, annualize, or
extrapolate anything.

## Must always appear

- Scope: accounts included, and accounts **excluded** with the reason.
- Assumptions, and for each, whether it came from the user or from a record.
- Validation results per check, including anything `incomplete`.
- Every limitation, in full. Limitations are not a footnote.
- Version pins: snapshot hash, engine manifest, calculation versions, evidence
  hash. These are what make the result reproducible later.
- A statement that this is prepared for professional review, is not tax advice,
  and authorizes nothing.

## Never

- Never describe a result as optimal.
- Never present a partial wash-sale screen as clearance.
- Never omit a limitation because it complicates the narrative.
