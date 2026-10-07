"""Scope resolution and access control.

Access derives from the authenticated principal. A subject name, tenant id or
account id supplied in natural language or by a model is a request, never a
grant, and a denial never discloses whether the object exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence

from ..domain import codes
from ..domain.identities import Account, Principal, Subject


class AccessDenied(PermissionError):
    def __init__(self, message: str = "The requested object is not available to this principal."):
        super().__init__(message)
        self.code = codes.ACCESS_DENIED


class AmbiguousSubject(ValueError):
    def __init__(self, candidates: Sequence[str]):
        super().__init__("More than one authorized subject matches this description.")
        self.code = codes.AMBIGUOUS_SUBJECT
        self.candidates = list(candidates)


class ScopeAuthorizer:
    def __init__(self, subjects: Iterable[Subject], accounts: Iterable[Account]):
        self._subjects = {s.subject_id: s for s in subjects}
        self._accounts = {a.account_id: a for a in accounts}

    def register(self, accounts: Iterable[Account], subjects: Iterable[Subject]) -> None:
        """Add account metadata discovered at runtime, e.g. from an upload.

        Entitlement is unaffected: registering an account does not grant access
        to it, and `resolve` still filters by the principal's entitlements.
        """
        for account in accounts:
            self._accounts[account.account_id] = account
        for subject in subjects:
            self._subjects[subject.subject_id] = subject

    def resolve(self, principal: Principal, subject_ref: str) -> Subject:
        subject = self._subjects.get(subject_ref)
        # Same response for "does not exist" and "not yours".
        if subject is None or subject.tenant_id != principal.tenant_id:
            raise AccessDenied()
        entitled = [
            account_id for account_id in subject.account_ids
            if account_id in principal.entitled_account_ids
        ]
        if not entitled:
            raise AccessDenied()
        return Subject(
            subject_id=subject.subject_id, tenant_id=subject.tenant_id, kind=subject.kind,
            account_ids=tuple(entitled),
            tax_unit_ids=tuple(sorted({
                self._accounts[a].tax_unit_id for a in entitled if a in self._accounts
            })),
        )

    def authorize_accounts(self, principal: Principal, account_ids: Sequence[str]) -> None:
        for account_id in account_ids:
            account = self._accounts.get(account_id)
            if (account is None or account.tenant_id != principal.tenant_id
                    or account_id not in principal.entitled_account_ids):
                raise AccessDenied()

    def related_accounts_in_tax_units(self, principal: Principal,
                                      tax_unit_ids: Sequence[str]) -> Sequence[str]:
        """Accounts we know belong to the tax unit, whether or not we can read them.

        Used so a wash-sale screen can say coverage is partial rather than
        implying that unseen accounts do not exist.
        """
        return sorted(
            a.account_id for a in self._accounts.values()
            if a.tenant_id == principal.tenant_id and a.tax_unit_id in set(tax_unit_ids)
        )
