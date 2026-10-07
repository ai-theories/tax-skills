"""Engine manifest and input provenance.

A result is reproducible only if everything it depended on is pinned: package
version, interpreter, calculation module versions, rule bundle, snapshot hash
and the hash of every input artifact.
"""
from __future__ import annotations

import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .. import CALCULATION_VERSIONS, __version__
from ..domain.hashing import content_hash


@dataclass(frozen=True)
class EngineManifest:
    package_version: str
    python_version: str
    platform_name: str
    calculation_versions: Dict[str, str]
    optimizer_name: str = ""
    optimizer_version: str = ""
    solver_settings: Dict[str, Any] = field(default_factory=dict)
    rule_bundle_ref: str = ""
    rule_bundle_hash: str = ""

    @classmethod
    def capture(cls, optimizer_name: str = "", optimizer_version: str = "",
                solver_settings: Optional[Dict[str, Any]] = None,
                rule_bundle_ref: str = "",
                rule_bundle_hash: str = "") -> "EngineManifest":
        return cls(
            package_version=__version__,
            python_version=sys.version.split()[0],
            platform_name=platform.platform(terse=True),
            calculation_versions=dict(CALCULATION_VERSIONS),
            optimizer_name=optimizer_name,
            optimizer_version=optimizer_version,
            # A deterministic rule pass and a repeatable solver are different
            # problems: record seed, threads and stopping criteria when a real
            # solver is bound, or replay cannot be claimed.
            solver_settings=dict(solver_settings or {"deterministic": True, "solver": "none"}),
            rule_bundle_ref=rule_bundle_ref,
            rule_bundle_hash=rule_bundle_hash,
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "package_version": self.package_version,
            "python_version": self.python_version,
            "platform": self.platform_name,
            "calculation_versions": self.calculation_versions,
            "optimizer": {"name": self.optimizer_name, "version": self.optimizer_version},
            "solver_settings": self.solver_settings,
            "rule_bundle_ref": self.rule_bundle_ref,
            "rule_bundle_hash": self.rule_bundle_hash,
        }


def input_provenance(**artifacts: Any) -> Dict[str, str]:
    """Hash each input artifact so a replay can prove it used the same inputs."""
    return {name: content_hash(payload) for name, payload in sorted(artifacts.items())}
