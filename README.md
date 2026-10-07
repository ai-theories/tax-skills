# tax-agent

An agent-facing tax analysis plugin backed by a deterministic financial service,
implementing the TaxAgentPlugin blueprint.

The plugin decides how to interpret a request, which skill applies, and how to
explain a result. The backend validates inputs, enforces scope, runs the
calculations and records reproducible evidence. **The plugin computes nothing.**

Status: **Phase 0 and Phase 1 of the blueprint** — contracts and an offline,
read-only account workflow. Analysis only: no code path places an order,
modifies a custodian record, or files a return.

## Quick start

```bash
cd tax-agent && python3 -m pytest tests/ -q
```

```bash
PYTHONPATH=src python3 examples/harvest_demo.py
```

A local dashboard runs the same scenarios in a browser:

```bash
python3 tax-agent/scripts/serve_dashboard.py
```

It binds `127.0.0.1:4180`, serves one page and a read-only JSON API, and has no
static passthrough. Every figure it shows comes from the same `AnalysisService`
the MCP tools call — nothing is calculated in the page.

The published site builds from the registries:

```bash
python3 tax-agent/scripts/build_site.py        # -> _site/ (index, llms.txt, sitemap)
python3 tax-agent/scripts/smoke_test.py        # check every scenario still behaves
```

Every capability claim on that page is read from `capabilities/`, `workflows/`,
`rules/tax/` and the skills' own frontmatter at build time, so the site cannot
outrun the code — an unavailable engine renders as unavailable rather than
being quietly dropped. `_site/index.html` is also served at `/site` by the
dashboard, where its live-scenario panel turns on.

## What it does today

An account-scoped harvest review, end to end: freeze a snapshot, reconcile it,
compile constraints, generate candidates, recompute gains, screen wash sales,
validate independently, and store a reproducible evidence package.

A **coordinated** household or UMA run: units (accounts, or sleeves) are served
in a declared order against one shared gain budget per **tax unit**, with
budget reservations so a concurrent run cannot spend the same allowance, and
cross-unit wash-sale prevention that includes managers' *planned* purchases —
the case that makes one UMA sleeve wash another sleeve's harvested loss.
Reported as `coordination: sequential`, `optimality_claimed: false` — and with
an **optimality gap**: an LP-relaxation ceiling on what any allocation of the
same budget could have raised, so the cost of coordinating rather than
optimizing is a measured number. A zero gap is a proof that sequential was
optimal for that instance.

## What it refuses to do, by design

| Request | Response |
|---|---|
| Joint household optimization | `UNSUPPORTED_SCOPE`. Coordination is available; a joint optimum is not, and the two are not the same claim. |
| "Keep gains under $15,000" without a netting basis or period | `needs_input` naming the exact fields. Gross and net give different answers. |
| A cash target that breaks the gain budget | `INFEASIBLE_CONSTRAINTS` with the shortfall. Constraints are never relaxed automatically. |
| Tax owed in dollars | No liability engine is bound, and the registry says so. |
| A lot with unknown basis | Blocked, not zeroed. |
| "Is this portfolio clear of wash sales?" | Never. `screened_complete` or `screened_with_gaps`, scoped to the accounts and dates actually covered. |

## Layout

```
contracts/v1/      versioned JSON schemas
capabilities/      engine coverage, tool and skill registries
workflows/         declarative workflow templates
src/taxagent/      domain, portfolio, tax, decisions, optimization,
                   validation, evidence, coordinator, storage, gateway
./ plugin.json, .mcp.json, 9 skills, 5 helper agents
rules/tax/         versioned rule packs, manifest, source index
_site/             generated landing page, llms.txt, sitemap
planned-skills/    designed, not enabled — no engine yet
tests/             194 tests, no network and no model required
```

## Design rules enforced in code

1. **Decimal money.** `Money.of(1.1)` raises. Floats never reach a basis.
2. **Unknown stays unknown.** Missing basis is `None` and blocks; it is never `0`.
3. **Independent validation.** The validator recomputes from the snapshot and
   ignores the engine's own success flag.
4. **Coverage separate from completion.** A finished calculation still reports
   what it could not see.
5. **Tenant scope server-side.** Derived from the authenticated principal; a
   tenant named in a document or argument is a request, not a grant.
6. **Documents are data.** The connector reads a fixed field set and drops
   everything else, so text in a statement cannot reach an instruction path.
7. **No optimality claims.** The selector reports `feasible`, never `optimal`.
8. **Coordination is not optimization, and the difference is measured.** The
   coordinator records its unit order, the rule that produced it,
   `order_dependent: true`, and the optimality gap against an LP bound.
9. **Tax rules live in a reviewed pack, never in source.** `rules/tax/` carries
   each rule with its authority, legal effective date and knowledge time. A
   constant reappearing in the calculation path fails the build, and a year no
   reviewed pack covers is refused rather than assumed.
10. **Reproducibility.** Evidence pins snapshot hash, rule bundle and its hash,
   engine manifest, calculation versions and input hashes; replay yields an
   identical digest.
11. **Capabilities need tests.** An engine claiming `implemented` with no
   reference test fails the suite.

## Not yet built

Phases 2-6 of the blueprint: a real optimizer adapter, a connected custodian
pilot, a validated tax-liability engine, and specialist asset classes.
See `docs/supported-capabilities.md` for the boundary, `docs/open-questions.md`
for what must be settled before production, and `docs/joint-optimization.md`
for the gate that governs when `scope: household` may be registered.
