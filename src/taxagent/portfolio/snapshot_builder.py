"""Snapshot construction and reconciliation.

Reconciliation records what is wrong with the source data; it never repairs it.
A missing basis stays missing and blocks the calculations that need it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..domain import codes
from ..domain.coverage import CoverageInterval, Finding
from ..domain.identities import Account
from ..domain.snapshots import Snapshot, compute_snapshot_hash
from ..domain.sleeves import Sleeve
from ..domain.tax_lots import BasisSource, TaxLot, Transaction


def build_snapshot(
    tenant_id: str,
    subject_id: str,
    as_of: date,
    accounts: Sequence[Account],
    lots: Sequence[TaxLot],
    transactions: Sequence[Transaction],
    coverage: Sequence[CoverageInterval],
    source_refs: Sequence[str] = (),
    reported_positions: Optional[Dict[Tuple[str, str], Decimal]] = None,
    snapshot_id: Optional[str] = None,
    sleeves: Sequence[Sleeve] = (),
    sleeve_assignments: Sequence[Tuple[str, str]] = (),
) -> Snapshot:
    findings: List[Finding] = []
    account_ids = {a.account_id for a in accounts}

    for account in accounts:
        if account.tenant_id != tenant_id:
            raise ValueError(f"account {account.account_id} belongs to another tenant")

    # --- lot-level checks ------------------------------------------------
    seen: Dict[str, TaxLot] = {}
    for lot in lots:
        if lot.lot_id in seen:
            findings.append(Finding.make(
                codes.DUPLICATE_LOT, codes.SEVERITY_BLOCKING,
                "Two lots share an identifier; holdings would be double counted.",
                lot_id=lot.lot_id, account_id=lot.account_id))
            continue
        seen[lot.lot_id] = lot

        if lot.account_id not in account_ids:
            findings.append(Finding.make(
                codes.UNRECONCILED_TRANSACTION, codes.SEVERITY_BLOCKING,
                "Lot references an account outside the snapshot scope.",
                lot_id=lot.lot_id, account_id=lot.account_id))
        if lot.quantity <= 0:
            findings.append(Finding.make(
                codes.INVALID_QUANTITY, codes.SEVERITY_BLOCKING,
                "Lot quantity must be positive.",
                lot_id=lot.lot_id, quantity=str(lot.quantity)))
        if not lot.basis_known:
            findings.append(Finding.make(
                codes.MISSING_BASIS, codes.SEVERITY_BLOCKING,
                "Basis is unknown; gain and loss calculations for this lot are blocked.",
                lot_id=lot.lot_id, account_id=lot.account_id, security_id=lot.security_id))
        if lot.acquisition_date is None:
            findings.append(Finding.make(
                codes.MISSING_ACQUISITION_DATE, codes.SEVERITY_BLOCKING,
                "Acquisition date is unknown; holding period cannot be determined.",
                lot_id=lot.lot_id, security_id=lot.security_id))
        if lot.basis_source in {BasisSource.TRANSFERRED_UNVERIFIED, BasisSource.CLIENT_SUPPLIED}:
            findings.append(Finding.make(
                codes.BASIS_PROVENANCE_UNVERIFIED, codes.SEVERITY_LIMITING,
                "Basis was not supplied by the custodian as covered; confirm before relying on it.",
                lot_id=lot.lot_id, basis_source=lot.basis_source.value))

    # --- transaction checks ---------------------------------------------
    for txn in transactions:
        if txn.account_id not in account_ids:
            findings.append(Finding.make(
                codes.UNRECONCILED_TRANSACTION, codes.SEVERITY_LIMITING,
                "Transaction references an account outside the snapshot scope.",
                txn_id=txn.txn_id, account_id=txn.account_id))

    # --- position reconciliation ----------------------------------------
    if reported_positions:
        derived: Dict[Tuple[str, str], Decimal] = {}
        for lot in seen.values():
            key = (lot.account_id, lot.security_id)
            derived[key] = derived.get(key, Decimal("0")) + lot.quantity
        for key, reported in reported_positions.items():
            held = derived.get(key, Decimal("0"))
            if held != reported:
                findings.append(Finding.make(
                    codes.POSITION_MISMATCH, codes.SEVERITY_BLOCKING,
                    "Sum of tax lots does not equal the reported position.",
                    account_id=key[0], security_id=key[1],
                    lot_total=str(held), reported=str(reported)))

    # --- coverage --------------------------------------------------------
    covered_accounts = {c.account_id for c in coverage}
    for account_id in sorted(account_ids - covered_accounts):
        findings.append(Finding.make(
            codes.COVERAGE_UNKNOWN, codes.SEVERITY_LIMITING,
            "No transaction-history coverage declared for this account.",
            account_id=account_id))
    for interval in coverage:
        if interval.start is None or interval.end is None:
            findings.append(Finding.make(
                codes.COVERAGE_UNKNOWN, codes.SEVERITY_LIMITING,
                "Transaction-history coverage window is unknown for this account.",
                account_id=interval.account_id))

    payload_accounts = tuple(sorted(accounts, key=lambda a: a.account_id))
    payload_lots = tuple(sorted(seen.values(), key=lambda l: l.lot_id))
    payload_txns = tuple(sorted(transactions, key=lambda t: (t.trade_date, t.txn_id)))
    payload_coverage = tuple(sorted(coverage, key=lambda c: c.account_id))
    # --- sleeve integrity ------------------------------------------------
    sleeve_ids = {s.sleeve_id for s in sleeves}
    for sleeve in sleeves:
        if sleeve.account_id not in account_ids:
            findings.append(Finding.make(
                codes.UNRECONCILED_TRANSACTION, codes.SEVERITY_BLOCKING,
                "Sleeve references an account outside the snapshot scope.",
                sleeve_id=sleeve.sleeve_id, account_id=sleeve.account_id))
    assigned_lots: Dict[str, str] = {}
    for lot_id, sleeve_id in sleeve_assignments:
        if sleeve_id not in sleeve_ids:
            findings.append(Finding.make(
                codes.UNRECONCILED_TRANSACTION, codes.SEVERITY_BLOCKING,
                "Lot assigned to a sleeve that is not in the snapshot.",
                lot_id=lot_id, sleeve_id=sleeve_id))
        elif lot_id in assigned_lots:
            findings.append(Finding.make(
                codes.DUPLICATE_LOT, codes.SEVERITY_BLOCKING,
                "Lot is assigned to more than one sleeve; it would be sold twice.",
                lot_id=lot_id, sleeve_id=sleeve_id, also=assigned_lots[lot_id]))
        else:
            assigned_lots[lot_id] = sleeve_id

    payload_findings = tuple(sorted(findings, key=lambda f: (f.code, f.refs)))
    payload_sleeves = tuple(sorted(sleeves, key=lambda s: s.sleeve_id))
    payload_assignments = tuple(sorted(tuple(pair) for pair in sleeve_assignments))

    draft = Snapshot(
        snapshot_id="pending",
        tenant_id=tenant_id,
        subject_id=subject_id,
        as_of=as_of,
        created_at=datetime.now(timezone.utc),
        accounts=payload_accounts,
        lots=payload_lots,
        transactions=payload_txns,
        coverage=payload_coverage,
        findings=payload_findings,
        source_refs=tuple(source_refs),
        content_hash="",
        sleeves=payload_sleeves,
        sleeve_assignments=payload_assignments,
    )
    digest = compute_snapshot_hash(draft.to_json())
    return Snapshot(
        snapshot_id=snapshot_id or f"snapshot_{uuid.uuid4().hex[:12]}",
        tenant_id=tenant_id,
        subject_id=subject_id,
        as_of=as_of,
        created_at=draft.created_at,
        accounts=payload_accounts,
        lots=payload_lots,
        transactions=payload_txns,
        coverage=payload_coverage,
        findings=payload_findings,
        source_refs=tuple(source_refs),
        content_hash=digest,
        sleeves=payload_sleeves,
        sleeve_assignments=payload_assignments,
    )
