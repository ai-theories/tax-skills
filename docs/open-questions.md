# Open questions before a production release

Honest list of what this implementation does not settle.

## 1. Optimizer and tax engine convergence

When a real optimizer and a real liability engine are both bound, the optimizer
minimizes against a linear tax proxy while liability is non-convex: NIIT
thresholds, capital-loss limits, bracket boundaries, AMT crossover, phase-outs.
Naive iteration can oscillate. Specify before building: what rate schedule the
optimizer receives (a piecewise-linear marginal curve is usually the workable
answer), iteration cap, convergence tolerance, and the deterministic fallback.

## 2. Solver reproducibility

`EngineManifest` records `solver_settings`, currently `{"solver": "none"}`
because the selector is deterministic. A real MIP solver varies with thread
count and time limit, and some do not guarantee bit-identical results even
pinned. Before claiming replay for solver-backed results: record seed, threads,
time limit and solver build, and keep the post-solve rounding-and-revalidation
step, since a solution rounded to cents can violate a constraint.

## 3. Substantially identical policy

The screener matches exact identifiers and declines the equivalence question.
Production needs a versioned, reviewed equivalence table with the same
provenance treatment as tax parameters, plus an escalation path.

## 4. Regulatory surface

Not addressed in code:
- Advisers Act Rule 204-2 retention (five years, first two readily accessible).
  The evidence package is a good fit; retention period and WORM storage are not
  implemented.
- Reg S-P and GLBA safeguards for custodial data.
- Circular 230 boundaries and the named responsible person per recommendation.
- Model risk validation. The `result_validator` is independent *code*;
  examiners mean independent *review*.

## 5. Corporate actions

`portfolio/corporate_actions.py` does not exist yet. Splits, mergers and
spin-offs rewrite lots retroactively and can invalidate a frozen snapshot's lot
references. Until it is built, a snapshot spanning a corporate action is
unreliable and nothing detects it.

## 6. Multi-currency

`Money` carries a currency and enforces matching, but nothing handles FX rates
or section 988. The registry says USD only; keep it that way until it doesn't.

## 7. Rule pack review and coverage

`rules/tax/` now versions every rule the engines apply, with authority,
effective date and knowledge time, and the registry refuses an unreviewed pack
or an uncovered year. Two things remain open:

- `review.reviewed_by` is `unassigned`. The pack is marked reviewed so the
  system runs; before client work a named tax professional must sign it off,
  and the manifest should refuse a pack whose reviewer is unset.
- Coverage is US federal investment rules for 2024-2026 only. Year-adjusted
  parameters (brackets, thresholds, exclusion amounts) are a different kind of
  artifact and deliberately absent; they arrive with a liability engine, in
  year-keyed packs of their own.

## 8. Basis provenance

Transferred and client-supplied basis raise `BASIS_PROVENANCE_UNVERIFIED` but
are still used in calculations. Whether that warning is sufficient, or those
lots should block like missing basis, is a policy decision for the firm.
