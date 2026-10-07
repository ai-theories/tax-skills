# Intake field requirements

## Accounts
`account_id`, `tax_unit_id`, `registration`, `custodian`, `owner_ref`.
Registration must be one of TAXABLE, JOINT_TAXABLE, TRADITIONAL_IRA, ROTH_IRA,
QUALIFIED_PLAN, TRUST_GRANTOR, TRUST_NON_GRANTOR. Registration drives wash-sale
treatment, so an unknown registration blocks screening rather than defaulting.

## Lots
`lot_id`, `account_id`, `security_id`, `quantity`, `acquisition_date`, `basis`,
`basis_source`, `covered`.

Unknown basis is `null`. Unknown acquisition date is `null`. Both block the
calculations that depend on them, which is the intended behaviour.

## Transactions
`txn_id`, `account_id`, `security_id`, `action`, `trade_date`, `quantity`,
`amount`. Actions: BUY, SELL, DIVIDEND_REINVEST, TRANSFER_IN, TRANSFER_OUT.
Dividend reinvestments matter: they are acquisitions and can trigger a wash sale.

## Coverage
For each account, the `start` and `end` of the transaction history held, plus a
`source_ref`. Missing coverage is recorded as unknown; it is never assumed to be
complete.

## Reported positions
Account, security and quantity as the custodian states them, so lot totals can
be reconciled against them.
