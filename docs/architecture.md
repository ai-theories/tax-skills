# Architecture

Two orchestrations, deliberately separated.

**Conversational orchestration** belongs to the host: interpret the request,
pick a skill, gather missing facts, explain the evidence. It lives in
`./`, which is Markdown and cannot enforce anything.

**Financial workflow orchestration** belongs to the backend: approved templates,
dependency order, scope checks, durable state, retries, cancellation. It lives
in `src/taxagent/` and cannot be bypassed by changing a prompt.

## Request path

```
host tool call
  -> MCP server            JSON-RPC, registered tools only
  -> ToolDispatcher        no generic execution entry point
  -> ScopeAuthorizer       tenant and entitlements, server-side
  -> CapabilityRegistry    refuses uncovered scope, asset, year
  -> WorkflowRunner        template order, attempts, checkpoints, cancel
       -> snapshot builder     freeze and reconcile; findings, never repairs
       -> constraint compiler  raises on ambiguity instead of defaulting
       -> harvest selector     candidate trades + self-reported status
       -> gain/loss            recompute per lot
       -> wash-sale screen     matches + coverage gaps + forward window
       -> result validator     independent recomputation; ignores the engine
       -> evidence builder     versions, hashes, claim map
  -> result envelope       status, validation, limitations, execution_authorized=false
```

## Why these boundaries

`domain/`, `portfolio/`, `tax/` and `validation/` import no storage, transport
or host SDK, so every financial test runs without a database or a model.

The validator does not read the engine's diagnostics. If it did, an engine that
reported success while breaching a constraint would pass — which is the failure
`test_engine_self_report_does_not_override_recomputation` exists to prevent.

The capability registry sits before the work, not after it. Refusing early is
what keeps a per-account loop from being presented as a household answer.

## Storage

SQLite stands in for PostgreSQL in the offline phase with the same shape: every
table tenant-scoped, runs unique on `(tenant_id, idempotency_key)`, step
attempts recorded, events sequenced for replay, artifacts content-addressed,
budget reservations with expiry so two sleeves cannot spend one allowance.

## State machine

`RECEIVED → VALIDATED → QUEUED → RUNNING → REVIEW_READY → COMPLETED`, with
`NEEDS_INPUT`, `BLOCKED`, `INVALIDATED`, `FAILED` and `CANCELLED`. Transitions
are asserted, retries stay inside a step's attempt history, and a scenario that
fails a hard check goes `REVIEW_READY → INVALIDATED` rather than reaching a
reviewer as a recommendation.
