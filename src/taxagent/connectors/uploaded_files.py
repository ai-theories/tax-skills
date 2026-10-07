"""Uploaded-document connector.

Documents are data. This loader reads a fixed set of typed fields and ignores
everything else in the file, so narrative text inside a statement, however it
is phrased, cannot reach an instruction path, change scope or widen
permissions. Text fields are carried only as opaque strings.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ..domain.coverage import CoverageInterval
from ..domain.identities import Account, Registration
from ..domain.money import Money, parse_quantity
from ..domain.sleeves import Sleeve
from ..domain.tax_lots import Action, BasisSource, TaxLot, Transaction, make_lot
from .interface import SourceData

# Only these keys are read. Anything else in the document is ignored entirely.
ACCOUNT_KEYS = {"account_id", "tax_unit_id", "registration", "custodian", "owner_ref"}
LOT_KEYS = {"lot_id", "account_id", "security_id", "quantity", "acquisition_date",
            "basis", "basis_source", "covered"}
TXN_KEYS = {"txn_id", "account_id", "security_id", "action", "trade_date", "quantity",
            "amount", "settle_date"}
SLEEVE_KEYS = {"sleeve_id", "account_id", "manager_ref", "model_ref", "target_weight"}


def _date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _money(value: Optional[str], currency: str = "USD") -> Optional[Money]:
    # A missing basis stays missing. It is never read as zero.
    return None if value in (None, "") else Money.of(str(value), currency)


class UploadedFileSource:
    """Loads a normalized JSON export produced by intake."""

    name = "uploaded-files"

    def load_payload(self, tenant_id: str, payload: Dict[str, Any],
                     source_ref: str = "upload") -> SourceData:
        accounts: List[Account] = []
        for raw in payload.get("accounts", []):
            fields = {k: raw.get(k) for k in ACCOUNT_KEYS}
            accounts.append(Account(
                account_id=str(fields["account_id"]),
                tenant_id=tenant_id,
                tax_unit_id=str(fields["tax_unit_id"]),
                registration=Registration(str(fields["registration"])),
                custodian=str(fields.get("custodian") or ""),
                owner_ref=str(fields.get("owner_ref") or ""),
            ))

        lots: List[TaxLot] = []
        for raw in payload.get("lots", []):
            fields = {k: raw.get(k) for k in LOT_KEYS}
            lots.append(make_lot(
                lot_id=str(fields["lot_id"]),
                account_id=str(fields["account_id"]),
                security_id=str(fields["security_id"]),
                quantity=str(fields["quantity"]),
                acquisition_date=_date(fields.get("acquisition_date")),
                basis=_money(fields.get("basis")),
                basis_source=BasisSource(str(fields.get("basis_source") or "UNKNOWN")),
                covered=fields.get("covered"),
            ))

        transactions: List[Transaction] = []
        for raw in payload.get("transactions", []):
            fields = {k: raw.get(k) for k in TXN_KEYS}
            transactions.append(Transaction(
                txn_id=str(fields["txn_id"]),
                account_id=str(fields["account_id"]),
                security_id=str(fields["security_id"]),
                action=Action(str(fields["action"])),
                trade_date=_date(fields["trade_date"]),
                quantity=parse_quantity(str(fields["quantity"])),
                amount=_money(fields.get("amount")),
                settle_date=_date(fields.get("settle_date")),
            ))

        coverage = tuple(
            CoverageInterval(
                account_id=str(raw["account_id"]),
                start=_date(raw.get("start")),
                end=_date(raw.get("end")),
                source_ref=str(raw.get("source_ref") or source_ref),
            )
            for raw in payload.get("coverage", [])
        )

        sleeves = tuple(
            Sleeve(
                sleeve_id=str(raw["sleeve_id"]), account_id=str(raw["account_id"]),
                manager_ref=str(raw.get("manager_ref") or ""),
                model_ref=str(raw.get("model_ref") or ""),
                target_weight=(str(raw["target_weight"])
                               if raw.get("target_weight") is not None else None),
            )
            for raw in payload.get("sleeves", [])
        )
        assignments = tuple(
            (str(raw["lot_id"]), str(raw["sleeve_id"]))
            for raw in payload.get("sleeve_assignments", [])
        )

        reported = {
            (str(raw["account_id"]), str(raw["security_id"])): Decimal(str(raw["quantity"]))
            for raw in payload.get("reported_positions", [])
        }

        return SourceData(tuple(accounts), tuple(lots), tuple(transactions), coverage,
                          (source_ref,), reported, sleeves, assignments)

    def load_file(self, tenant_id: str, path: str) -> SourceData:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return self.load_payload(tenant_id, payload, source_ref=f"file:{path}")
