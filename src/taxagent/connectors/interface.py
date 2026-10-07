"""Source connector port."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Protocol, Sequence, Tuple

from ..domain.coverage import CoverageInterval
from ..domain.identities import Account
from ..domain.sleeves import Sleeve
from ..domain.tax_lots import TaxLot, Transaction


@dataclass(frozen=True)
class SourceData:
    accounts: Tuple[Account, ...]
    lots: Tuple[TaxLot, ...]
    transactions: Tuple[Transaction, ...]
    coverage: Tuple[CoverageInterval, ...]
    source_refs: Tuple[str, ...]
    reported_positions: Dict[Tuple[str, str], Any]
    sleeves: Tuple[Sleeve, ...] = ()
    sleeve_assignments: Tuple[Tuple[str, str], ...] = ()


class SourcePort(Protocol):
    def load(self, tenant_id: str, document_ref: str) -> SourceData: ...
