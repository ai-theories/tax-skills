from datetime import date
from decimal import Decimal

from taxagent.domain import codes
from taxagent.domain.coverage import CoverageInterval
from taxagent.domain.identities import Account, Registration
from taxagent.domain.money import Money
from taxagent.domain.tax_lots import BasisSource, make_lot
from taxagent.portfolio.snapshot_builder import build_snapshot
from taxagent.tax.lot_gain_loss import ProposedSale, calculate_gain_loss


def test_known_answer_long_term_loss(simple_snapshot):
    result = calculate_gain_loss(
        simple_snapshot,
        [ProposedSale("acct_tax", "ARKK", Decimal("100"), Money.of("62.00"),
                      lot_id="lot_loss", sale_date=date(2026, 9, 30))])
    disposition = result.dispositions[0]
    assert disposition.proceeds.to_json() == "6200.00"
    assert disposition.basis.to_json() == "12000.00"
    assert disposition.gain.to_json() == "-5800.00"
    assert disposition.character.value == "LONG_TERM"
    assert result.net_gain.to_json() == "-5800.00"


def test_partial_lot_sale_prorates_basis(simple_snapshot):
    result = calculate_gain_loss(
        simple_snapshot,
        [ProposedSale("acct_tax", "ARKK", Decimal("25"), Money.of("62.00"),
                      lot_id="lot_loss", sale_date=date(2026, 9, 30))])
    assert result.dispositions[0].basis.to_json() == "3000.00"


def test_missing_basis_blocks_instead_of_defaulting_to_zero():
    accounts = [Account("acct_tax", "tenant_ria_1", "tu_1", Registration.TAXABLE, "Schwab", "o")]
    lots = [make_lot("lot_x", "acct_tax", "TLT", "100", __import__("datetime").date(2024, 6, 1),
                     None, BasisSource.TRANSFERRED_UNVERIFIED, False)]
    snapshot = build_snapshot("tenant_ria_1", "acct_tax", date(2026, 9, 30), accounts, lots, [],
                              [CoverageInterval("acct_tax", date(2024, 1, 1), date(2026, 9, 30), "s")])
    result = calculate_gain_loss(
        snapshot, [ProposedSale("acct_tax", "TLT", Decimal("100"), Money.of("88.00"),
                                lot_id="lot_x")])
    disposition = result.dispositions[0]
    assert disposition.blocked is True
    assert disposition.block_code == codes.MISSING_BASIS
    assert disposition.gain is None
    # A blocked lot contributes nothing rather than a misleading zero gain.
    assert result.net_gain.to_json() == "0.00"
    assert result.calculated == ()


def test_gross_and_net_gain_are_different_measures(simple_snapshot):
    result = calculate_gain_loss(simple_snapshot, [
        ProposedSale("acct_tax", "VTI", Decimal("100"), Money.of("150.00"), lot_id="lot_gain"),
        ProposedSale("acct_tax", "ARKK", Decimal("100"), Money.of("62.00"), lot_id="lot_loss"),
    ], date(2026, 9, 30))
    assert result.gross_gains.to_json() == "5000.00"
    assert result.gross_losses.to_json() == "-5800.00"
    assert result.net_gain.to_json() == "-800.00"
