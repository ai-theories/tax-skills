# Planned skills — not enabled

These are design placeholders for V2 and later. They are deliberately **not**
in `skills/`, because a skill that advertises a tool the
backend does not implement is how a capability registry starts lying.

| Planned skill | Blocked on |
|---|---|
| `tax-transition` | A validated optimizer with risk inputs and multi-period paths. |
| `transition-frontier` | The above, plus an agreed objective normalization. |
| `muni-taxable-compare` | Security-level federal/state/AMT metadata with provenance, and dated cash flows. |
| `bond-accrual-review` | OID, acquisition premium, market discount and bond premium engines with reviewed fixtures. |
| `after-tax-performance` | A fixed benchmark, liquidation assumption, cash-flow method and rate convention. |

Each becomes a real skill only when its engine appears in
`capabilities/engines.yaml` with passing reference tests.
