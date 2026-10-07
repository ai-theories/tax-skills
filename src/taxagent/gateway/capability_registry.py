"""Capability and coverage registry.

The registry is the reason an unsupported request produces UNSUPPORTED_SCOPE
instead of a plausible-looking answer. It also refuses to advertise a
capability whose reference tests are not listed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from ..domain import codes
from ..optimization.interface import UnsupportedCapability


@dataclass(frozen=True)
class EngineCoverage:
    engine_id: str
    version: str
    kind: str
    status: str
    scopes: Tuple[str, ...]
    asset_classes: Tuple[str, ...]
    jurisdictions: Tuple[str, ...]
    tax_years: Tuple[int, ...]
    reference_tests: Tuple[str, ...]
    limitations: Tuple[str, ...]
    claims_optimality: bool = False

    @property
    def is_available(self) -> bool:
        return self.status == "implemented" and bool(self.reference_tests)

    def to_json(self) -> Dict[str, Any]:
        return {
            "engine_id": self.engine_id, "version": self.version, "kind": self.kind,
            "status": self.status, "scopes": list(self.scopes),
            "asset_classes": list(self.asset_classes), "jurisdictions": list(self.jurisdictions),
            "tax_years": list(self.tax_years), "claims_optimality": self.claims_optimality,
            "reference_tests": list(self.reference_tests), "limitations": list(self.limitations),
        }


class CapabilityRegistry:
    def __init__(self, engines: Sequence[EngineCoverage], tools: Sequence[Dict[str, Any]],
                 skills: Sequence[Dict[str, Any]], hosts: Sequence[Dict[str, Any]] = ()):
        self.engines = tuple(engines)
        self.tools = tuple(tools)
        self.skills = tuple(skills)
        self.hosts = tuple(hosts)
        self._servers: Sequence[Dict[str, Any]] = ()

    @classmethod
    def load(cls, directory: str) -> "CapabilityRegistry":
        def read(name: str, key: str) -> List[Dict[str, Any]]:
            path = os.path.join(directory, name)
            if not os.path.exists(path):
                return []
            with open(path, "r", encoding="utf-8") as handle:
                return yaml.safe_load(handle).get(key, []) or []

        engines = [
            EngineCoverage(
                engine_id=e["engine_id"], version=str(e.get("version", "")),
                kind=e.get("kind", ""), status=e.get("status", "unavailable"),
                scopes=tuple(e.get("scopes") or ()), asset_classes=tuple(e.get("asset_classes") or ()),
                jurisdictions=tuple(e.get("jurisdictions") or ()),
                tax_years=tuple(int(y) for y in (e.get("tax_years") or ())),
                reference_tests=tuple(e.get("reference_tests") or ()),
                limitations=tuple(e.get("limitations") or ()),
                claims_optimality=bool(e.get("claims_optimality", False)),
            )
            for e in read("engines.yaml", "engines")
        ]
        registry = cls(engines, read("tools.yaml", "tools"), read("skills.yaml", "skills"),
                       read("host-compatibility.yaml", "hosts"))
        registry._servers = read("tools.yaml", "servers")
        return registry

    # --- queries ---------------------------------------------------------
    def engine(self, engine_id: str) -> Optional[EngineCoverage]:
        return next((e for e in self.engines if e.engine_id == engine_id), None)

    def implemented_tools(self, server: Optional[str] = None) -> Tuple[str, ...]:
        """Tools a caller can actually invoke, for one server or across all.

        Each server exposes its own surface, so a bare list would merge three
        of them and let a tool missing from the server that claims it pass
        unnoticed. Naming the server is how the guard stays bidirectional.
        """
        return tuple(t["name"] for t in self.tools
                     if t.get("status") == "implemented"
                     and (server is None or t.get("server") == server))

    def servers(self) -> Tuple[Dict[str, Any], ...]:
        """The servers this plugin ships, as the registry declares them."""
        return tuple(dict(s) for s in self._servers)

    def internal_operations(self) -> Tuple[str, ...]:
        """Workflow steps. Real code, but not reachable from a tool call."""
        return tuple(t["name"] for t in self.tools
                     if t.get("status") == "internal_operation")

    def require(self, kind: str, scope: str, asset_classes: Sequence[str],
                tax_year: Optional[int] = None,
                jurisdiction: str = "US_FEDERAL") -> EngineCoverage:
        """Return an engine that actually covers this request, or raise."""
        candidates = [e for e in self.engines if e.kind == kind and e.is_available]
        if not candidates:
            raise UnsupportedCapability(
                codes.UNSUPPORTED_SCOPE,
                f"No validated {kind} is available in this deployment.")

        in_scope = [e for e in candidates if scope in e.scopes]
        if not in_scope:
            alternative = sorted({s for e in candidates for s in e.scopes})
            raise UnsupportedCapability(
                codes.UNSUPPORTED_SCOPE,
                f"No {kind} covers '{scope}' scope. Running a {alternative} engine repeatedly "
                f"would not produce a {scope}-level result and must not be described as one.",
                supported_alternative=", ".join(alternative) or None)

        unsupported_assets = [
            a for a in asset_classes
            if not any(a in e.asset_classes for e in in_scope)
        ]
        if unsupported_assets:
            raise UnsupportedCapability(
                codes.UNSUPPORTED_ASSET,
                f"Asset classes not covered by any available {kind}: "
                f"{', '.join(sorted(set(unsupported_assets)))}.")

        covering = [
            e for e in in_scope
            if all(a in e.asset_classes for a in asset_classes)
            and (tax_year is None or tax_year in e.tax_years)
            and jurisdiction in e.jurisdictions
        ]
        if not covering:
            raise UnsupportedCapability(
                codes.WRONG_RULE_YEAR,
                f"No {kind} covers tax year {tax_year} in {jurisdiction} for the requested "
                "asset classes.")
        return covering[0]

    def to_json(self) -> Dict[str, Any]:
        return {
            "engines": [e.to_json() for e in self.engines],
            "servers": [dict(s) for s in self._servers],
            "tools": [dict(t) for t in self.tools if t.get("status") == "implemented"],
            "internal_operations": [dict(t) for t in self.tools
                                    if t.get("status") == "internal_operation"],
            "skills": [dict(s) for s in self.skills],
            "host_compatibility": [dict(h) for h in self.hosts],
            "execution_capabilities": [],
            "note": "Analysis only. No operation in this release places orders or files returns.",
        }
