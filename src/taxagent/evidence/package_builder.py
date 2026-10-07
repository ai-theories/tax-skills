"""Evidence package assembly.

Every number a narrative may state must exist as a field here, and claim_map
records where each one comes from. The report generator receives this package
and nothing else; it has no access to the ledger.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from ..domain.constraints import ConstraintSet
from ..domain.hashing import content_hash
from ..domain.snapshots import Snapshot
from ..optimization.interface import Candidate
from ..tax.lot_gain_loss import GainLossResult
from ..tax.wash_sale_screen import WashSaleScreenResult
from ..validation.result_validator import ValidationResult
from .provenance import EngineManifest, input_provenance

# Identity and wall-clock fields are excluded from the content hash: replaying
# the same inputs must produce the same digest even though the run, request and
# artifact identifiers are new each time.
VOLATILE_TOP_LEVEL = ("evidence_id", "run_id", "request_id", "created_at", "content_hash")
VOLATILE_NESTED = {"inputs": ("snapshot_ref",), "constraints": ("constraints_ref",)}

CLAIM_MAP = {
    "cash_raised": "/results/gain_loss/totals/total_proceeds",
    "net_realized_gain": "/results/gain_loss/totals/net_gain",
    "gross_realized_gains": "/results/gain_loss/totals/gross_gains",
    "harvested_losses": "/results/gain_loss/totals/gross_losses",
    "short_term_gain": "/results/gain_loss/totals/short_term_gain",
    "long_term_gain": "/results/gain_loss/totals/long_term_gain",
    "disallowed_loss": "/results/wash_sale/total_disallowed_loss",
    "wash_sale_status": "/results/wash_sale/status",
    "gain_budget_residual": "/results/validation/recomputed/gain_budget/residual",
    "trade_count": "/results/validation/recomputed/trade_count",
}

#: Added only when the engine reported a gap, so every claim in claim_map
#: always resolves to a live field.
COORDINATION_CLAIMS = {
    "optimality_gap": "/results/candidate/diagnostics/optimality_gap/gap",
    "optimality_bound": "/results/candidate/diagnostics/optimality_gap/bound_cash",
    "provably_optimal": "/results/candidate/diagnostics/optimality_gap/provably_optimal",
    "allocation_order": "/results/candidate/diagnostics/allocation_order",
}


def build_evidence_package(
    run_id: str,
    request_id: str,
    tenant_id: str,
    snapshot: Snapshot,
    constraints: ConstraintSet,
    candidate: Optional[Candidate],
    gain_loss: GainLossResult,
    screen: Optional[WashSaleScreenResult],
    validation: ValidationResult,
    manifest: EngineManifest,
    interpreted_request: Dict[str, Any],
    assumptions: Optional[List[Dict[str, str]]] = None,
    evidence_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    limitations: List[Dict[str, Any]] = [f.to_json() for f in validation.limitations]

    package: Dict[str, Any] = {
        "schema_version": "1.0",
        "evidence_id": evidence_id or f"evidence_{uuid.uuid4().hex[:12]}",
        "run_id": run_id,
        "request_id": request_id,
        "tenant_id": tenant_id,
        "mode": "analysis_only",
        "execution_authorized": False,
        "interpreted_request": interpreted_request,
        "scope": {
            "subject_id": snapshot.subject_id,
            "as_of": snapshot.as_of.isoformat(),
            "accounts_included": list(snapshot.account_ids()),
            "accounts_missing_basis": list(snapshot.accounts_missing_basis()),
        },
        "inputs": {
            "snapshot_ref": snapshot.snapshot_id,
            "snapshot_hash": snapshot.content_hash,
            "constraints": constraints.to_json(),
            "source_refs": list(snapshot.source_refs),
        },
        "results": {
            # None, not an empty candidate: a review that proposed nothing must
            # not leave a shape in evidence that reads as a proposal of nothing.
            "candidate": candidate.to_json() if candidate else None,
            "gain_loss": gain_loss.to_json(),
            "wash_sale": screen.to_json() if screen else None,
            "validation": validation.to_json(),
        },
        "assumptions": assumptions or [],
        "limitations": limitations,
        "engine_manifest": manifest.to_json(),
        "claim_map": _claim_map(candidate),
        "provenance": {
            # The snapshot's own hash already excludes its identifier.
            "snapshot": snapshot.content_hash,
            "constraints": content_hash(_material_constraints(constraints.to_json())),
            "candidate": content_hash(candidate.to_json() if candidate else None),
        },
    }
    # created_at is recorded but excluded from the hash so that replaying the
    # same inputs produces the same digest.
    package["content_hash"] = content_hash(_material(package))
    package["created_at"] = (created_at or datetime.now(timezone.utc)).isoformat()
    return package


def _claim_map(candidate) -> Dict[str, str]:
    claims = dict(CLAIM_MAP)
    diagnostics = getattr(candidate, "diagnostics", None) or {}
    if "optimality_gap" in diagnostics:
        claims.update(COORDINATION_CLAIMS)
    return claims


def _material_constraints(constraints: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in constraints.items() if k not in VOLATILE_NESTED["constraints"]}


def _material(package: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a package that a replay must reproduce exactly."""
    material = {k: v for k, v in package.items() if k not in VOLATILE_TOP_LEVEL}
    inputs = dict(material.get("inputs", {}))
    for key in VOLATILE_NESTED["inputs"]:
        inputs.pop(key, None)
    if "constraints" in inputs:
        inputs["constraints"] = _material_constraints(inputs["constraints"])
    material["inputs"] = inputs
    return material


def resolve_claim(package: Dict[str, Any], claim: str) -> Any:
    """Resolve a claim's pointer, or None if this package does not make it.

    An unknown claim name is still an error — the map is fixed, and a typo
    there should fail loudly. But a pointer into a result the run never
    produced is not an error: a review that proposes nothing has no trade
    count, and reporting that as absent is the correct answer rather than a
    crash.
    """
    pointer = package["claim_map"][claim]
    node: Any = package
    for part in pointer.strip("/").split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
