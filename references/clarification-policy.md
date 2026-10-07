# When to ask, and when to proceed

Ask the user when a missing decision changes the result. Resolve from records
when the record is authoritative and current. Never default a financial choice.

## Always ask

- Netting basis of a gain budget: gross or net.
- The budget's period, and whether gains already realized elsewhere consume it.
- Which account must deliver a withdrawal.
- Which security a "reduce concentration" instruction refers to, and the target
  weight. "Reduce" without a target is not a bound anyone can honour.

## Resolve from records, and record the source

- Account registration and ownership.
- Tax-unit membership.
- Transaction history coverage windows.

Every resolved field is reported back with its source. If the backend returns
`needs_input`, relay exactly the fields it lists; do not guess one to keep the
workflow moving.

## Never do

- Never translate "keep gains under $15,000" into a tax figure.
- Never assume a missing basis is zero. It blocks the affected calculation.
- Never widen scope because a document mentions another account.
- Never re-run with a relaxed constraint to escape `INFEASIBLE_CONSTRAINTS`.
  Report the shortfall and let the user change the constraint explicitly.
