"""The servers this plugin ships, and how each one is built.

Three processes rather than one, split where the trust boundary actually
falls. The federal server holds no client data and needs no entitlement, so it
can be installed on its own. The portfolio and household servers each carry an
authenticated principal and refuse anything outside it; they are separate from
each other because household coordination makes claims about shared budgets
and cross-sleeve screening that account-scope analysis does not, and an
operator should be able to run one without the other.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple

VERSION = "0.1.0"


@dataclass(frozen=True)
class ServerSpec:
    name: str
    summary: str
    instructions: str
    needs_identity: bool
    build: Callable[[], Any]


def _identity() -> Tuple[str, str, List[str]]:
    tenant_id = os.environ.get("TAXAGENT_TENANT_ID")
    principal_id = os.environ.get("TAXAGENT_PRINCIPAL_ID")
    accounts = [a for a in os.environ.get("TAXAGENT_ACCOUNTS", "").split(",") if a]
    if not (tenant_id and principal_id and accounts):
        sys.stderr.write(
            "TAXAGENT_TENANT_ID, TAXAGENT_PRINCIPAL_ID and TAXAGENT_ACCOUNTS must be set. "
            "This offline server has no identity provider and will not infer a principal.\n")
        raise SystemExit(2)
    return tenant_id, principal_id, accounts


def _workflow_dispatcher(cls):
    """Build a dispatcher that carries an authenticated principal."""
    from ..application.bootstrap import build_service
    from ..connectors.uploaded_files import UploadedFileSource
    from ..domain.identities import Principal, Subject

    tenant_id, principal_id, accounts = _identity()
    principal = Principal(principal_id, tenant_id, frozenset({"analysis"}), frozenset(accounts))
    service = build_service([], [])
    source = UploadedFileSource()

    def load(document_ref: str):
        """Load a document and register the accounts it describes.

        The uploaded file is the source of account metadata: registration,
        ownership and tax unit. Registering them does not grant access — the
        principal's entitlements still decide what is readable — but without it
        every subject would resolve to nothing.
        """
        data = source.load_file(tenant_id, document_ref)
        subjects = [
            Subject(account.account_id, tenant_id, "account",
                    (account.account_id,), (account.tax_unit_id,))
            for account in data.accounts
        ]
        for tax_unit_id in sorted({a.tax_unit_id for a in data.accounts}):
            members = tuple(a.account_id for a in data.accounts
                            if a.tax_unit_id == tax_unit_id)
            subjects.append(Subject(f"household_{tax_unit_id}", tenant_id, "household",
                                    members, (tax_unit_id,)))
        service.authorizer.register(data.accounts, subjects)
        return data

    return cls(service, principal, document_loader=load)


def _calculator(domain: str):
    """A stateless calculation server for one domain."""
    from .calculator_dispatcher import CalculatorDispatcher
    return lambda: CalculatorDispatcher(domain)


def _portfolio():
    from .domain_dispatchers import PortfolioDispatcher
    return _workflow_dispatcher(PortfolioDispatcher)


def _household():
    from .domain_dispatchers import HouseholdDispatcher
    return _workflow_dispatcher(HouseholdDispatcher)


_ANALYSIS_ONLY = ("Analysis only. No tool in this server places orders, modifies custodian "
                  "records or files returns.")

_CALCULATION_NOTE = (" This server holds no client data: every figure arrives in the "
                     "call and nothing is stored. Results are estimates over those "
                     "figures, not a return and not tax advice.")

# The six calculation servers. They are separate processes rather than one
# because an operator should be able to run the ones they need: a firm with no
# international clients should not be installing an expatriation surface, and
# a server that is not installed cannot be called by mistake.
_CALCULATION_SERVERS = {
    "federal": ("US federal tax calculations over figures you supply.", ""),
    "estate": ("Federal estate tax, gift exclusions and fiduciary income tax.",
               " The compressed fiduciary brackets and the unified credit are computed; "
               "what belongs in the gross estate, and what it is worth, are not."),
    "international": ("Foreign earned income exclusion and foreign gift reporting.",
                      " Eligibility for the exclusion is never established here: it "
                      "turns on where someone lived and worked."),
    "business": ("Section 179 expensing and C corporation versus pass-through.",
                 " The comparison is arithmetic over rates the caller supplies. Entity "
                 "choice is not a calculation and this server does not pretend it is."),
    "compliance": ("Late filing and payment penalties, and the estimated tax safe "
                   "harbour.",
                   " Interest is never computed: the rate is set quarterly and "
                   "compounds daily, so no rate is stored here."),
    "muni": ("Municipal bond yields, market discount and premium amortization.",
             " Whether a bond is a private activity bond, and what any state charges, "
             "are supplied by the caller rather than looked up."),
    "salt": ("The federal cap on deducting state and local tax.",
             " No state's own income tax is computed. No state pack is bound in this "
             "deployment, and the cap limits a federal deduction rather than telling "
             "you what a state charges."),
}

SERVERS: Dict[str, ServerSpec] = {
    key: ServerSpec(
        name=f"taxagent-{key}",
        summary=summary,
        instructions=_ANALYSIS_ONLY + _CALCULATION_NOTE + extra,
        needs_identity=False,
        build=_calculator(key))
    for key, (summary, extra) in _CALCULATION_SERVERS.items()
}

SERVERS.update({
    "portfolio": ServerSpec(
        name="taxagent-portfolio",
        summary="Account-scope lot analysis: gain and loss, wash-sale screening, "
                "loss-harvest scenarios.",
        instructions=_ANALYSIS_ONLY + " Every result is scoped to the accounts this "
                     "principal is entitled to read; an account named in an argument is a "
                     "request, not a grant. Unknown basis blocks a lot rather than "
                     "defaulting to zero.",
        needs_identity=True, build=_portfolio),
    "household": ServerSpec(
        name="taxagent-household",
        summary="Household and UMA-sleeve coordination over one shared budget per tax unit.",
        instructions=_ANALYSIS_ONLY + " Two engines with different claims: "
                     "optimize_household solves the whole household at once and, where "
                     "the search exhausts, proves no other selection does better; "
                     "coordinate_household serves accounts in a declared order, reserves "
                     "budget as it goes, and is never optimal. Optimality is over the "
                     "lots, prices and constraints supplied and for one objective, not a "
                     "judgement about what the household should do. Budgets are allocated "
                     "per tax unit: a household spanning several tax units gets several "
                     "budgets and never one pooled allowance.",
        needs_identity=True, build=_household),
})


def spec(name: str) -> ServerSpec:
    if name not in SERVERS:
        raise SystemExit(
            f"Unknown server '{name}'. Available: {', '.join(sorted(SERVERS))}.")
    return SERVERS[name]
