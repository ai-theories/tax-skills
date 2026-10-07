"""Result envelopes returned across the MCP boundary.

Workflow status, coverage and validation are separate fields on purpose: a
calculation can complete while its real-world coverage is incomplete, and the
explaining agent must be able to see the difference.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

SCHEMA_VERSION = "1.0"


def _base(request_id: str, status: str) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "status": status,
        "execution_authorized": False,
    }


def completed(request_id: str, *, result_ref: Optional[str] = None,
              evidence_ref: Optional[str] = None, snapshot_ref: Optional[str] = None,
              validation: Optional[Dict[str, str]] = None,
              limitations: Sequence[Dict[str, str]] = (),
              engine_manifest_ref: Optional[str] = None,
              data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    status = "completed_with_limitations" if limitations else "completed"
    envelope = _base(request_id, status)
    envelope.update({
        "result_ref": result_ref, "evidence_ref": evidence_ref, "snapshot_ref": snapshot_ref,
        "engine_manifest_ref": engine_manifest_ref, "validation": validation or {},
        "limitations": [dict(l) for l in limitations], "data": data or {},
    })
    return envelope


def needs_input(request_id: str, unresolved_fields: Sequence[str], message: str) -> Dict[str, Any]:
    envelope = _base(request_id, "needs_input")
    envelope.update({"unresolved_fields": list(unresolved_fields), "message": message})
    return envelope


def blocked(request_id: str, code: str, message: str,
            supported_alternative: Optional[str] = None) -> Dict[str, Any]:
    envelope = _base(request_id, "blocked")
    envelope.update({"code": code, "message": message,
                     "supported_alternative": supported_alternative})
    return envelope


def failed(request_id: str, code: str, message: str) -> Dict[str, Any]:
    envelope = _base(request_id, "failed")
    envelope.update({"code": code, "message": message})
    return envelope


def replayed(request_id: str, run_id: str) -> Dict[str, Any]:
    """The idempotency key has already produced a finished run.

    Returning the ordinary `accepted` envelope here told the caller work had
    been queued and to poll for it, when in fact nothing was queued and the
    answer already existed. An agent reading that either polls a run that will
    never change state, or concludes it has started a second analysis.
    """
    envelope = _base(request_id, "accepted")
    envelope.update({
        "job_ref": run_id,
        "replay": True,
        "message": "This idempotency key already produced a completed run; nothing was "
                   "run again. Fetch it with get_run_status, or get_scenario for the "
                   "evidence package.",
    })
    return envelope


def accepted(request_id: str, run_id: str) -> Dict[str, Any]:
    envelope = _base(request_id, "accepted")
    envelope.update({"job_ref": run_id,
                     "message": "Run accepted. Poll get_run_status; transport success is not "
                                "workflow completion."})
    return envelope
