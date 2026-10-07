"""Identity, ownership and entitlement types.

A household is an operational grouping. It is not automatically one taxpayer
and not automatically one authorization domain, so tax-unit scope and access
scope are modelled separately and never inferred from each other.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Optional, Tuple


class Registration(str, Enum):
    TAXABLE = "TAXABLE"
    JOINT_TAXABLE = "JOINT_TAXABLE"
    TRADITIONAL_IRA = "TRADITIONAL_IRA"
    ROTH_IRA = "ROTH_IRA"
    QUALIFIED_PLAN = "QUALIFIED_PLAN"
    TRUST_GRANTOR = "TRUST_GRANTOR"
    TRUST_NON_GRANTOR = "TRUST_NON_GRANTOR"

    @property
    def is_retirement(self) -> bool:
        """Accounts where a replacement purchase triggers Rev. Rul. 2008-5."""
        return self in {
            Registration.TRADITIONAL_IRA,
            Registration.ROTH_IRA,
            Registration.QUALIFIED_PLAN,
        }

    @property
    def is_taxable(self) -> bool:
        return self in {
            Registration.TAXABLE,
            Registration.JOINT_TAXABLE,
            Registration.TRUST_GRANTOR,
            Registration.TRUST_NON_GRANTOR,
        }


@dataclass(frozen=True)
class Account:
    account_id: str
    tenant_id: str
    tax_unit_id: str
    registration: Registration
    custodian: str
    owner_ref: str
    subject_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Subject:
    """An analysis target: one account, or a household of accounts."""

    subject_id: str
    tenant_id: str
    kind: str  # 'account' | 'household'
    account_ids: Tuple[str, ...]
    tax_unit_ids: Tuple[str, ...]

    @property
    def spans_multiple_tax_units(self) -> bool:
        return len(set(self.tax_unit_ids)) > 1


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. Entitlements are resolved server-side.

    A tenant identifier supplied by a model or by natural language is ignored;
    only this object, derived from the authenticated session, grants access.
    """

    principal_id: str
    tenant_id: str
    scopes: FrozenSet[str] = field(default_factory=frozenset)
    entitled_account_ids: FrozenSet[str] = field(default_factory=frozenset)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes
