"""TaxAgent deterministic financial analysis backend.

Read-only analysis only. This package exposes no operation that places orders,
mutates custodian records, or files returns.
"""
__version__ = "0.1.0"
CALCULATION_VERSIONS = {
    "lot_accounting": "1.0.0",
    "lot_gain_loss": "1.0.0",
    "wash_sale_screen": "1.0.0",
    "harvest_selector": "1.0.0",
    "exact_solver": "1.0.0",
    "oracle_adapter": "1.0.0",
    "household_optimizer": "1.0.0",
    "rebalance_engine": "1.0.0",
    "result_validator": "1.0.0",
}
