# Tax analysis review pack

**Run:** `{{run_id}}` · **Prepared:** {{created_at}} · **Mode:** analysis only

## 1. Request as interpreted
{{interpreted_request}}

Unresolved fields at submission, and how each was settled:
{{resolutions}}

## 2. Scope
- Subject: {{subject_id}} · as of {{as_of}}
- Accounts included: {{accounts_included}}
- Accounts excluded, with reason: {{accounts_excluded}}
- Tax units in scope: {{tax_unit_ids}}

## 3. Assumptions
{{assumptions}}

## 4. Result
| Measure | Value | Evidence field |
|---|---|---|
| Cash raised | {{cash_raised}} | `/results/gain_loss/totals/total_proceeds` |
| Losses harvested | {{harvested_losses}} | `/results/gain_loss/totals/gross_losses` |
| Net realized gain | {{net_realized_gain}} | `/results/gain_loss/totals/net_gain` |
| Short-term | {{short_term_gain}} | `/results/gain_loss/totals/short_term_gain` |
| Long-term | {{long_term_gain}} | `/results/gain_loss/totals/long_term_gain` |
| Gain budget residual | {{gain_budget_residual}} | `/results/validation/recomputed/gain_budget/residual` |
| Disallowed loss | {{disallowed_loss}} | `/results/wash_sale/total_disallowed_loss` |

## 5. Validation
{{validation_checks}}

## 6. Limitations
{{limitations}}

## 7. Reproducibility
- Snapshot hash: {{snapshot_hash}}
- Rule bundle: {{rule_bundle_ref}}
- Engine manifest: {{engine_manifest}}
- Evidence hash: {{content_hash}}

## 8. Reviewer sign-off
- [ ] Scope and exclusions confirmed
- [ ] Assumptions confirmed with the client
- [ ] Limitations understood and acceptable
- [ ] Wash-sale coverage understood, including accounts not screened

Reviewed by: ____________________  Date: __________

Prepared for professional review. Not tax advice. No trade is authorized by
this document.
