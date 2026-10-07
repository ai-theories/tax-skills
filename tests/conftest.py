import os
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from taxagent.application.bootstrap import build_service  # noqa: E402
from taxagent.connectors.uploaded_files import UploadedFileSource  # noqa: E402
from taxagent.domain.coverage import CoverageInterval  # noqa: E402
from taxagent.domain.identities import Account, Principal, Registration, Subject  # noqa: E402
from taxagent.domain.money import Money  # noqa: E402
from taxagent.domain.tax_lots import Action, BasisSource, Transaction, make_lot  # noqa: E402
from taxagent.portfolio.snapshot_builder import build_snapshot  # noqa: E402
from taxagent.storage.sqlite import SqliteStore  # noqa: E402

TENANT = "tenant_ria_1"
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "patel_household.json")


@pytest.fixture
def tenant():
    return TENANT


@pytest.fixture
def accounts():
    return [
        Account("acct_schwab_joint", TENANT, "tu_patel", Registration.JOINT_TAXABLE,
                "Schwab", "owner_patel"),
        Account("acct_fid_roth", TENANT, "tu_patel", Registration.ROTH_IRA,
                "Fidelity", "owner_patel"),
        Account("acct_spouse_ext", TENANT, "tu_patel", Registration.TAXABLE,
                "Vanguard", "owner_spouse"),
        Account("acct_other_tenant", "tenant_ria_2", "tu_other", Registration.TAXABLE,
                "Schwab", "owner_other"),
        Account("acct_uma", TENANT, "tu_patel", Registration.TAXABLE, "Schwab", "owner_patel"),
    ]


@pytest.fixture
def subjects():
    return [
        Subject("acct_schwab_joint", TENANT, "account",
                ("acct_schwab_joint",), ("tu_patel",)),
        Subject("household_patel", TENANT, "household",
                ("acct_schwab_joint", "acct_fid_roth"), ("tu_patel",)),
        Subject("household_other", "tenant_ria_2", "household",
                ("acct_other_tenant",), ("tu_other",)),
        Subject("acct_uma", TENANT, "account", ("acct_uma",), ("tu_patel",)),
    ]


@pytest.fixture
def principal():
    return Principal("adv_1", TENANT, frozenset({"analysis"}),
                     frozenset({"acct_schwab_joint", "acct_fid_roth", "acct_uma"}))


@pytest.fixture
def store():
    return SqliteStore()


@pytest.fixture
def service(subjects, accounts, store):
    return build_service(subjects, accounts, store=store)


@pytest.fixture
def source_data():
    return UploadedFileSource().load_file(TENANT, FIXTURE)


@pytest.fixture
def prices():
    return {"VTI": "260.00", "AAPL": "205.00", "ARKK": "62.00", "TLT": "88.00"}


@pytest.fixture
def harvest_intent():
    return {
        "schema_version": "1.0",
        "request_id": "req_1042",
        "intent": "harvest_review",
        "mode": "analysis_only",
        "subject_ref": "acct_schwab_joint",
        "objectives": [{"type": "raise_cash", "amount": "10000.00", "currency": "USD",
                        "withdrawal_account": "acct_schwab_joint"}],
        "constraints": [{"type": "realized_gain_budget", "amount": "15000.00",
                         "currency": "USD", "netting_basis": "net_gains", "period": "2026"}],
    }


@pytest.fixture
def simple_snapshot():
    """One taxable account and one Roth, one gain lot and one loss lot."""
    accounts = [
        Account("acct_tax", TENANT, "tu_1", Registration.TAXABLE, "Schwab", "o1"),
        Account("acct_roth", TENANT, "tu_1", Registration.ROTH_IRA, "Fidelity", "o1"),
    ]
    lots = [
        make_lot("lot_gain", "acct_tax", "VTI", "100", date(2020, 1, 10),
                 Money.of("10000.00"), BasisSource.CUSTODIAN_COVERED, True),
        make_lot("lot_loss", "acct_tax", "ARKK", "100", date(2025, 1, 10),
                 Money.of("12000.00"), BasisSource.CUSTODIAN_COVERED, True),
    ]
    coverage = [
        CoverageInterval("acct_tax", date(2019, 1, 1), date(2026, 9, 30), "src_a"),
        CoverageInterval("acct_roth", date(2019, 1, 1), date(2026, 9, 30), "src_b"),
    ]
    return build_snapshot(TENANT, "acct_tax", date(2026, 9, 30), accounts, lots, [],
                          coverage, ["fixture"])
