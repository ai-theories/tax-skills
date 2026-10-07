# Oracle integration plan

> **Status, 2026-10-06: bound.** `oracle-account-adapter` is `implemented` with
> `claims_optimality: true`, bound to the in-tree exact solver
> (`engine_build: exact-solver/1.0.0`) rather than to an external vendor build.
> The conformance gate described below was cleared: all twelve optimality cases
> pass. The adapter interface is unchanged, so an external engine can still be
> bound in place of the reference one — that is what `engine_build` records.
>
> The account-scope limit in §1 still holds and is enforced: household scope
> raises UNSUPPORTED_SCOPE even though the solver could serve it, because
> calling an account optimizer once per account is not joint optimization.

`capabilities/engines.yaml` lists `oracle-account-adapter` as `not_configured`.
This is what it would take to change that, and what the adapter must satisfy
before it does.

## 1. What Oracle is, and what it is not

Double Finance's Oracle is a tax-aware portfolio optimizer. Two limits come
straight from its own documentation and cannot be engineered around:

- **Account scope only.** It optimizes one account. Calling it once per account
  is not household optimization, and an adapter cannot manufacture the
  capability. `household` scope stays refused; `household_coordinated` is
  served by the sequential coordinator, which never claims optimality.
- **No sector constraints.** Any constraint family it does not accept must be
  declared unsupported rather than approximated by post-filtering its output,
  which would silently change the problem being solved.

Pin a commit before integrating. A README is a claim about a moving target, and
the adapter's behaviour has to be tied to a build.

## 2. Where it plugs in

`OptimizerPort` already defines the contract: `solve(problem) -> Candidate`.
Nothing above it changes. The adapter's job is translation and honesty:

```
OptimizationProblem          ->  Oracle's problem format
Oracle's solution            ->  Candidate(trades, solver_status, diagnostics)
Anything Oracle cannot take  ->  UnsupportedCapability, before any work
```

The independent validator recomputes every figure from the snapshot afterwards
and ignores whatever status the adapter reports, so a translation error becomes
a failed validation rather than a wrong recommendation.

## 3. Use-case taxonomy

The conformance suite in `tests/cases/optimizer/` encodes these as 122 scripted
cases over synthetic portfolios. Each case names the rules it exercises and is
checked against invariants recomputed from the snapshot.

| Category | Cases | What it pins |
|---|---|---|
| conservation | 12 | Only real lots, real quantities, no lot sold twice |
| gain_budget | 16 | Gross vs net netting, external realized, exact boundaries |
| cash_target | 12 | Met, missed, zero, unreachable, never met by breaching a budget |
| restrictions | 12 | Do-not-sell, frozen accounts, minimum holding periods |
| wash_sale | 14 | Existing and *planned* replacements, Roth, cross-unit conflicts |
| selection | 10 | Forced choices where only one answer is possible |
| infeasibility | 10 | Reported with a shortfall and a reason, never relaxed |
| data_quality | 10 | Missing basis, missing date, missing price are excluded |
| determinism | 6 | Two solves of one problem give identical trades |
| scope | 8 | Refusals, and what coordination may and may not claim |
| optimality | 12 | **Gated.** Only an engine claiming optimality runs these |

## 4. The optimality gate

Twelve cases carry `requires: [optimality]` and are skipped for any adapter
that does not claim it. They are the measured distance between "feasible" and
"optimal", and the rule-based selector would fail several by construction. For
example, `opt-spends-budget-on-the-better-lot`: a 5,000 budget buys 15,000 of
cash from one lot or 5,000 from another, and greedy ordering takes the small
one first and then cannot afford the large one.

`test_optimality_cases_are_gated_not_silently_passing` fails the moment an
adapter claims optimality without the cases going green, and
`test_no_registered_engine_claims_optimality_without_passing_the_gate` fails if
`engines.yaml` claims it for an engine the suite does not cover.

## 5. What the adapter must supply beyond trades

| Requirement | Why |
|---|---|
| `solver_status` from the engine, untranslated | `optimal` must mean proven, not "it finished" |
| Seed, thread count, time limit, solver build in `solver_settings` | Replay is otherwise unprovable; MIP solvers vary with threads |
| Post-solve rounding, then revalidation | A solution rounded to whole shares can breach a constraint the solver satisfied in floats |
| An optimality gap when stopped early | A time-limited run reports a bound, it does not claim an optimum |
| An irreducible infeasible set | "Infeasible" is useless at portfolio scale without naming the conflicting constraints |
| Refusal before work for unsupported input | Cheaper, and it keeps partial results from looking like answers |

## 6. Gate: what must hold before `status: implemented`

1. All 122 conformance cases pass, including the 12 optimality cases.
2. A determinism harness: identical trades across 20 runs and at least two
   thread counts, with the manifest pinned.
3. Rounding repair verified — a fractional solution that would breach a
   constraint is caught and corrected.
4. Infeasibility returns an IIS mapped to named constraints.
5. A pinned commit and build digest recorded in `EngineManifest`.
6. `reference_tests` in `engines.yaml` naming the cases that justify the claim,
   which `test_every_advertised_capability_has_reference_tests` enforces.

Until all six hold, the adapter refuses and the registry says `not_configured`.
That is the intended behaviour, not a gap to be closed by lowering the bar.

## 7. Sequence

| Step | Outcome |
|---|---|
| Pin a commit; map `OptimizationProblem` to Oracle's format | Translation exists, nothing registered |
| Run the non-optimality cases against it | Proves it is at least as sound as the selector |
| Add determinism and rounding harnesses | Makes replay claimable |
| Run the optimality gate | Decides whether `claims_optimality` is honest |
| Register in `engines.yaml` with reference tests | The capability becomes advertisable |
