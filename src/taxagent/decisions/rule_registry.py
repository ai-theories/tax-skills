"""Versioned tax rule packs.

The calculation engines read every tax rule from here. Nothing in the
calculation path may hardcode one, because a constant in source has no version,
no effective date, no authority and nothing to catch it drifting — which is the
exact failure mode that produces a confident answer computed from a stale
number.

Two clocks are kept so a historical run reproduces what we believed then:
`legal_effective_from` is when a rule governs a taxpayer; `knowledge_time` is
when we recorded it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import yaml

from ..domain import codes
from ..domain.hashing import content_hash

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RULES_DIR = os.path.join(PROJECT_ROOT, "rules", "tax")


class RulePackError(ValueError):
    """A pack is missing, unreviewed, or does not cover the request."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TaxRules:
    """The resolved rules one calculation may use."""

    bundle_id: str
    bundle_hash: str
    jurisdiction: str
    tax_year: int
    knowledge_time: str
    wash_sale_window_days: int
    long_term_holding_period_years: int
    permanently_disallowing_registrations: FrozenSet[str]
    replacement_matching_basis: str
    authorities: Dict[str, str] = field(default_factory=dict)

    @property
    def bundle_ref(self) -> str:
        return self.bundle_id

    def disallows_permanently(self, registration_value: str) -> bool:
        return registration_value in self.permanently_disallowing_registrations

    def to_json(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_hash": self.bundle_hash,
            "jurisdiction": self.jurisdiction,
            "tax_year": self.tax_year,
            "knowledge_time": self.knowledge_time,
            "values": {
                "wash_sale_window_days": self.wash_sale_window_days,
                "long_term_holding_period_years": self.long_term_holding_period_years,
                "permanently_disallowing_registrations": sorted(
                    self.permanently_disallowing_registrations),
                "replacement_matching_basis": self.replacement_matching_basis,
            },
            "authorities": dict(self.authorities),
        }


@dataclass(frozen=True)
class IncomeRules:
    """Year-adjusted federal income parameters for one tax year."""

    bundle_id: str
    bundle_hash: str
    tax_year: int
    knowledge_time: str
    ordinary_brackets: Dict[str, Tuple[Tuple[Decimal, Optional[Decimal]], ...]]
    standard_deduction: Dict[str, Decimal]
    capital_gain_brackets: Dict[str, Tuple[Tuple[Decimal, Optional[Decimal]], ...]]
    niit_rate: Decimal
    niit_thresholds: Dict[str, Decimal]
    capital_loss_limit: Dict[str, Decimal]
    amt: Dict[str, Any] = field(default_factory=dict)
    qbi: Dict[str, Any] = field(default_factory=dict)
    authorities: Dict[str, str] = field(default_factory=dict)

    def for_status(self, table: Dict[str, Any], status: str) -> Any:
        if status not in table:
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"filing status {status!r} is not in this pack; "
                f"known: {sorted(table)}")
        return table[status]

    def to_json(self) -> Dict[str, Any]:
        return {"bundle_id": self.bundle_id, "bundle_hash": self.bundle_hash,
                "tax_year": self.tax_year, "knowledge_time": self.knowledge_time,
                "authorities": dict(self.authorities)}


@dataclass(frozen=True)
class DomainRules:
    """Parameters for one tax domain and one year, read from a reviewed pack.

    The income and investment packs each have a typed dataclass because engines
    read their fields by name in many places. The newer domains are each read
    by one engine, so a typed shape per domain would be five near-identical
    classes and five near-identical resolvers. This carries the parameters as
    decimalised data and keeps every guarantee that matters: the pack must be
    reviewed, must cover the year, and every rule's effective date is checked
    against the analysis date before a figure is handed out.
    """

    domain: str
    bundle_id: str
    bundle_hash: str
    jurisdiction: str
    tax_year: int
    knowledge_time: str
    params: Dict[str, Any]
    authorities: Dict[str, str]

    def get(self, name: str) -> Any:
        if name not in self.params:
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"'{name}' is not in {self.bundle_id}; known: {sorted(self.params)}")
        return self.params[name]

    def for_status(self, table: Dict[str, Any], status: str) -> Any:
        if status not in table:
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"filing status {status!r} is not in this pack; known: {sorted(table)}")
        return table[status]

    def to_json(self) -> Dict[str, Any]:
        return {"domain": self.domain, "bundle_id": self.bundle_id,
                "bundle_hash": self.bundle_hash, "tax_year": self.tax_year,
                "knowledge_time": self.knowledge_time,
                "authorities": dict(self.authorities)}


_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def _decimalise(node: Any) -> Any:
    """Numeric strings become Decimals; everything else keeps its own type.

    Packs hold more than money. A rule's effective window is a date, a
    treatment is a name, and a list of qualifying years is a list. Converting
    every string blindly meant a pack could only hold figures: a boolean once
    crashed the loader, and the workaround was to move a real parameter into
    code, where it stops being reviewable. Only values that are entirely
    numeric are converted, so a date or an enum survives as written.
    """
    if isinstance(node, dict):
        return {k: _decimalise(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_decimalise(v) for v in node]
    if isinstance(node, str):
        return Decimal(node) if _NUMERIC.match(node) else node
    return node


def _bands(raw) -> Tuple[Tuple[Decimal, Optional[Decimal]], ...]:
    return tuple(
        (Decimal(b["rate"]), None if b["up_to"] is None else Decimal(b["up_to"]))
        for b in raw)


class RuleRegistry:
    def __init__(self, packs: Dict[str, Dict[str, Any]], defaults: Dict[str, Dict[int, str]],
                 income_defaults: Optional[Dict[str, Dict[int, str]]] = None,
                 domain_defaults: Optional[Dict[str, Dict[str, Dict[int, str]]]] = None):
        self._packs = packs
        self._defaults = defaults
        self._income_defaults = income_defaults or {}
        self._domain_defaults = domain_defaults or {}

    @classmethod
    def load(cls, directory: str = RULES_DIR) -> "RuleRegistry":
        manifest_path = os.path.join(directory, "manifests", "index.yaml")
        if not os.path.exists(manifest_path):
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"No rule manifest at {manifest_path}. Calculations cannot run without "
                "a reviewed rule pack.")
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = yaml.safe_load(handle)

        packs: Dict[str, Dict[str, Any]] = {}
        for entry in manifest.get("bundles", []):
            path = os.path.join(directory, "parameter-packs", entry["file"])
            with open(path, "r", encoding="utf-8") as handle:
                pack = yaml.safe_load(handle)
            if pack["bundle_id"] != entry["bundle_id"]:
                raise RulePackError(
                    codes.WRONG_RULE_YEAR,
                    f"Manifest lists {entry['bundle_id']} but the file declares "
                    f"{pack['bundle_id']}.")
            packs[pack["bundle_id"]] = pack

        defaults = {
            jurisdiction: {int(year): bundle for year, bundle in years.items()}
            for jurisdiction, years in (manifest.get("default_bundles") or {}).items()
        }
        income_defaults = {
            jurisdiction: {int(year): bundle for year, bundle in years.items()}
            for jurisdiction, years in (manifest.get("income_bundles") or {}).items()
        }
        domain_defaults = {
            jurisdiction: {
                domain: {int(year): bundle for year, bundle in years.items()}
                for domain, years in domains.items()
            }
            for jurisdiction, domains in (manifest.get("domain_bundles") or {}).items()
        }
        return cls(packs, defaults, income_defaults, domain_defaults)

    # --- resolution ------------------------------------------------------
    def resolve(self, tax_year: int, jurisdiction: str = "US_FEDERAL",
                as_of: Optional[date] = None, bundle_id: Optional[str] = None) -> TaxRules:
        if bundle_id is None:
            bundle_id = self._defaults.get(jurisdiction, {}).get(tax_year)
        if bundle_id is None:
            covered = sorted(self._defaults.get(jurisdiction, {}))
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"No reviewed rule pack covers {jurisdiction} {tax_year}. "
                f"Covered years: {covered or 'none'}.")

        pack = self._packs.get(bundle_id)
        if pack is None:
            raise RulePackError(codes.WRONG_RULE_YEAR, f"Unknown rule bundle {bundle_id}.")
        if pack.get("status") != "reviewed":
            # An unreviewed pack is a draft. Refusing beats computing from it.
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"Rule bundle {bundle_id} has status '{pack.get('status')}'. Only reviewed "
                "packs may be used for calculations.")
        if tax_year not in [int(y) for y in pack.get("tax_years", [])]:
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"Bundle {bundle_id} does not cover tax year {tax_year}.")

        rules = pack["rules"]
        effective_as_of = as_of or date(tax_year, 12, 31)
        for name, rule in rules.items():
            effective = date.fromisoformat(rule["legal_effective_from"])
            if effective > effective_as_of:
                raise RulePackError(
                    codes.WRONG_RULE_YEAR,
                    f"Rule '{name}' in {bundle_id} takes effect {effective.isoformat()}, "
                    f"after the {effective_as_of.isoformat()} date of this analysis.")

        return TaxRules(
            bundle_id=bundle_id,
            bundle_hash=content_hash(pack),
            jurisdiction=pack["jurisdiction"],
            tax_year=tax_year,
            knowledge_time=str(pack.get("knowledge_time", "")),
            wash_sale_window_days=int(rules["wash_sale_window_days"]["value"]),
            long_term_holding_period_years=int(rules["long_term_holding_period_years"]["value"]),
            permanently_disallowing_registrations=frozenset(
                rules["permanently_disallowing_registrations"]["value"]),
            replacement_matching_basis=str(rules["replacement_matching_basis"]["value"]),
            authorities={name: rule["authority"] for name, rule in rules.items()},
        )

    def resolve_income(self, tax_year: int, jurisdiction: str = "US_FEDERAL",
                       bundle_id: Optional[str] = None) -> IncomeRules:
        if bundle_id is None:
            bundle_id = self._income_defaults.get(jurisdiction, {}).get(tax_year)
        if bundle_id is None:
            covered = sorted(self._income_defaults.get(jurisdiction, {}))
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"No reviewed income pack covers {jurisdiction} {tax_year}. "
                f"Covered years: {covered or 'none'}.")
        pack = self._packs.get(bundle_id)
        if pack is None:
            raise RulePackError(codes.WRONG_RULE_YEAR, f"Unknown rule bundle {bundle_id}.")
        if pack.get("status") != "reviewed":
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"Rule bundle {bundle_id} has status {pack.get('status')!r}.")

        rules = pack["rules"]
        niit = rules["niit"]["value"]
        return IncomeRules(
            bundle_id=bundle_id,
            bundle_hash=content_hash(pack),
            tax_year=tax_year,
            knowledge_time=str(pack.get("knowledge_time", "")),
            ordinary_brackets={k: _bands(v)
                               for k, v in rules["ordinary_brackets"]["value"].items()},
            standard_deduction={k: Decimal(v)
                                for k, v in rules["standard_deduction"]["value"].items()},
            capital_gain_brackets={k: _bands(v)
                                   for k, v in rules["capital_gain_brackets"]["value"].items()},
            niit_rate=Decimal(niit["rate"]),
            niit_thresholds={k: Decimal(v) for k, v in niit["thresholds"].items()},
            capital_loss_limit={k: Decimal(v)
                                for k, v in rules["capital_loss_limit"]["value"].items()},
            amt=_decimalise(rules["amt"]["value"]),
            qbi=_decimalise(rules["qbi"]["value"]),
            authorities={name: rule["authority"] for name, rule in rules.items()},
        )

    def resolve_domain(self, domain: str, tax_year: int,
                       jurisdiction: str = "US_FEDERAL",
                       as_of: Optional[date] = None,
                       bundle_id: Optional[str] = None) -> DomainRules:
        """Resolve one domain's pack, with the same gates as every other pack."""
        if bundle_id is None:
            bundle_id = (self._domain_defaults.get(jurisdiction, {})
                         .get(domain, {}).get(tax_year))
        if bundle_id is None:
            covered = sorted((self._domain_defaults.get(jurisdiction, {})
                              .get(domain, {})))
            known = sorted(self._domain_defaults.get(jurisdiction, {}))
            if not covered and domain not in known:
                raise RulePackError(
                    codes.UNSUPPORTED_SCOPE,
                    f"No pack of any year covers the '{domain}' domain in {jurisdiction}. "
                    f"Domains available: {known or 'none'}.")
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"No reviewed {domain} pack covers {jurisdiction} {tax_year}. "
                f"Covered years: {covered or 'none'}.")

        pack = self._packs.get(bundle_id)
        if pack is None:
            raise RulePackError(codes.WRONG_RULE_YEAR, f"Unknown rule bundle {bundle_id}.")
        if pack.get("status") != "reviewed":
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"Rule bundle {bundle_id} has status {pack.get('status')!r}. Only reviewed "
                "packs may be used for calculations.")
        if tax_year not in [int(y) for y in pack.get("tax_years", [])]:
            raise RulePackError(
                codes.WRONG_RULE_YEAR,
                f"Bundle {bundle_id} does not cover tax year {tax_year}.")

        rules = pack["rules"]
        effective_as_of = as_of or date(tax_year, 12, 31)
        for name, rule in rules.items():
            effective = date.fromisoformat(rule["legal_effective_from"])
            if effective > effective_as_of:
                raise RulePackError(
                    codes.WRONG_RULE_YEAR,
                    f"Rule '{name}' in {bundle_id} takes effect {effective.isoformat()}, "
                    f"after the {effective_as_of.isoformat()} date of this analysis.")

        return DomainRules(
            domain=domain, bundle_id=bundle_id, bundle_hash=content_hash(pack),
            jurisdiction=pack["jurisdiction"], tax_year=tax_year,
            knowledge_time=str(pack.get("knowledge_time", "")),
            params={name: _decimalise(rule["value"]) for name, rule in rules.items()},
            authorities={name: rule["authority"] for name, rule in rules.items()},
        )

    def domains(self, jurisdiction: str = "US_FEDERAL") -> List[str]:
        return sorted(self._domain_defaults.get(jurisdiction, {}))

    def bundle_ids(self) -> List[str]:
        return sorted(self._packs)


@lru_cache(maxsize=8)
def _cached_registry(directory: str) -> RuleRegistry:
    return RuleRegistry.load(directory)


def income_rules(tax_year: int, jurisdiction: str = "US_FEDERAL",
                 directory: str = RULES_DIR) -> IncomeRules:
    """Year-adjusted federal parameters. No hardcoded fallback, by design."""
    return _cached_registry(directory).resolve_income(tax_year, jurisdiction)


def domain_rules(domain: str, tax_year: int, jurisdiction: str = "US_FEDERAL",
                 as_of: Optional[date] = None, directory: str = RULES_DIR) -> DomainRules:
    """Parameters for one domain. No hardcoded fallback, by design."""
    return _cached_registry(directory).resolve_domain(domain, tax_year, jurisdiction, as_of)


def available_domains(jurisdiction: str = "US_FEDERAL",
                      directory: str = RULES_DIR) -> List[str]:
    return _cached_registry(directory).domains(jurisdiction)


def default_rules(tax_year: int, jurisdiction: str = "US_FEDERAL",
                  as_of: Optional[date] = None, directory: str = RULES_DIR) -> TaxRules:
    """Resolve the reviewed pack for a tax year.

    There is deliberately no hardcoded fallback: if no reviewed pack covers the
    year, the calculation is refused rather than computed from an assumption.
    """
    return _cached_registry(directory).resolve(tax_year, jurisdiction, as_of)
