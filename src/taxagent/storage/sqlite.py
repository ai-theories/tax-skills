"""SQLite implementation of the storage ports.

SQLite here stands in for PostgreSQL in the offline phase: same schema shape,
same tenant filtering, same idempotency and lease semantics.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..domain.hashing import content_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
  state TEXT NOT NULL, payload TEXT NOT NULL, detail TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (tenant_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, run_id TEXT NOT NULL,
  step_id TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
  detail TEXT, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, run_id TEXT NOT NULL,
  seq INTEGER NOT NULL, event TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_ref TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, kind TEXT NOT NULL,
  payload TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
  reservation_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, tax_unit_id TEXT NOT NULL,
  period TEXT NOT NULL, amount TEXT NOT NULL, run_id TEXT NOT NULL,
  state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  expires_at TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteStore:
    """Implements RunRepository, EventStore, ArtifactStore and ReservationStore."""

    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- runs ------------------------------------------------------------
    def create_or_get(self, tenant_id: str, idempotency_key: str,
                      payload: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        existing = self.conn.execute(
            "SELECT * FROM runs WHERE tenant_id = ? AND idempotency_key = ?",
            (tenant_id, idempotency_key)).fetchone()
        if existing:
            return self._run_row(existing), False
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.conn.execute(
            "INSERT INTO runs (run_id, tenant_id, idempotency_key, state, payload, "
            "detail, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, tenant_id, idempotency_key, "RECEIVED", json.dumps(payload), None, now, now))
        self.conn.commit()
        return self._run_row(self.conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()), True

    def get(self, tenant_id: str, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE tenant_id = ? AND run_id = ?", (tenant_id, run_id)).fetchone()
        return self._run_row(row) if row else None

    def set_state(self, tenant_id: str, run_id: str, state: str,
                  detail: Optional[Dict[str, Any]] = None) -> None:
        self.conn.execute(
            "UPDATE runs SET state = ?, detail = ?, updated_at = ? "
            "WHERE tenant_id = ? AND run_id = ?",
            (state, json.dumps(detail) if detail else None, _now(), tenant_id, run_id))
        self.conn.commit()

    def record_step(self, tenant_id: str, run_id: str, step_id: str, attempt: int,
                    status: str, detail: Optional[Dict[str, Any]] = None) -> None:
        self.conn.execute(
            "INSERT INTO steps (tenant_id, run_id, step_id, attempt, status, detail, recorded_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (tenant_id, run_id, step_id, attempt, status,
             json.dumps(detail) if detail else None, _now()))
        self.conn.commit()

    def steps(self, tenant_id: str, run_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT step_id, attempt, status, detail FROM steps "
            "WHERE tenant_id = ? AND run_id = ? ORDER BY id", (tenant_id, run_id)).fetchall()
        return [
            {"step_id": r["step_id"], "attempt": r["attempt"], "status": r["status"],
             "detail": json.loads(r["detail"]) if r["detail"] else None}
            for r in rows
        ]

    def request_cancel(self, tenant_id: str, run_id: str) -> None:
        self.conn.execute(
            "UPDATE runs SET cancel_requested = 1, updated_at = ? "
            "WHERE tenant_id = ? AND run_id = ?", (_now(), tenant_id, run_id))
        self.conn.commit()

    def is_cancel_requested(self, tenant_id: str, run_id: str) -> bool:
        row = self.conn.execute(
            "SELECT cancel_requested FROM runs WHERE tenant_id = ? AND run_id = ?",
            (tenant_id, run_id)).fetchone()
        return bool(row and row["cancel_requested"])

    @staticmethod
    def _run_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "run_id": row["run_id"], "tenant_id": row["tenant_id"],
            "idempotency_key": row["idempotency_key"], "state": row["state"],
            "payload": json.loads(row["payload"]),
            "detail": json.loads(row["detail"]) if row["detail"] else None,
            "cancel_requested": bool(row["cancel_requested"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    # --- events ----------------------------------------------------------
    def append(self, tenant_id: str, run_id: str, event: Dict[str, Any]) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS s FROM events WHERE tenant_id = ? AND run_id = ?",
            (tenant_id, run_id)).fetchone()
        seq = int(row["s"]) + 1
        self.conn.execute(
            "INSERT INTO events (tenant_id, run_id, seq, event, recorded_at) VALUES (?,?,?,?,?)",
            (tenant_id, run_id, seq, json.dumps({**event, "seq": seq}), _now()))
        self.conn.commit()
        return seq

    def events(self, tenant_id: str, run_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT event FROM events WHERE tenant_id = ? AND run_id = ? ORDER BY seq",
            (tenant_id, run_id)).fetchall()
        return [json.loads(r["event"]) for r in rows]

    # --- artifacts -------------------------------------------------------
    def put(self, tenant_id: str, kind: str, payload: Dict[str, Any]) -> str:
        digest = content_hash(payload)
        existing = self.conn.execute(
            "SELECT artifact_ref FROM artifacts WHERE tenant_id = ? AND content_hash = ? "
            "AND kind = ?", (tenant_id, digest, kind)).fetchone()
        if existing:
            return existing["artifact_ref"]
        artifact_ref = f"{kind}_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            "INSERT INTO artifacts (artifact_ref, tenant_id, kind, payload, content_hash, "
            "created_at) VALUES (?,?,?,?,?,?)",
            (artifact_ref, tenant_id, kind, json.dumps(payload), digest, _now()))
        self.conn.commit()
        return artifact_ref

    def get_artifact(self, tenant_id: str, artifact_ref: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT payload FROM artifacts WHERE tenant_id = ? AND artifact_ref = ?",
            (tenant_id, artifact_ref)).fetchone()
        return json.loads(row["payload"]) if row else None

    # --- shared budget reservations --------------------------------------
    def reserve(self, tenant_id: str, tax_unit_id: str, period: str, amount: str,
                run_id: str, budget_limit: str, expires_at: str) -> Tuple[bool, str]:
        """Reserve gain-budget capacity for a tax unit.

        Two sleeves cannot consume the same allowance: the check and the insert
        happen in one transaction, and expired reservations release capacity.
        """
        with self.conn:
            self.conn.execute(
                "UPDATE reservations SET state = 'EXPIRED' WHERE tenant_id = ? AND state = "
                "'ACTIVE' AND expires_at < ?", (tenant_id, _now()))
            rows = self.conn.execute(
                "SELECT amount FROM reservations WHERE tenant_id = ? AND tax_unit_id = ? "
                "AND period = ? AND state = 'ACTIVE'", (tenant_id, tax_unit_id, period)).fetchall()
            outstanding = sum((Decimal(r["amount"]) for r in rows), Decimal("0"))
            if outstanding + Decimal(amount) > Decimal(budget_limit):
                return False, (
                    f"Requested {amount} would exceed the {period} budget for {tax_unit_id}: "
                    f"{outstanding} of {budget_limit} is already reserved by another run."
                )
            reservation_id = f"resv_{uuid.uuid4().hex[:10]}"
            self.conn.execute(
                "INSERT INTO reservations (reservation_id, tenant_id, tax_unit_id, period, "
                "amount, run_id, state, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (reservation_id, tenant_id, tax_unit_id, period, amount, run_id,
                 "ACTIVE", expires_at, _now()))
        return True, reservation_id

    def release(self, tenant_id: str, reservation_id: str) -> None:
        self.conn.execute(
            "UPDATE reservations SET state = 'RELEASED' WHERE tenant_id = ? AND "
            "reservation_id = ?", (tenant_id, reservation_id))
        self.conn.commit()

    def outstanding(self, tenant_id: str, tax_unit_id: str, period: str) -> str:
        rows = self.conn.execute(
            "SELECT amount FROM reservations WHERE tenant_id = ? AND tax_unit_id = ? "
            "AND period = ? AND state = 'ACTIVE'", (tenant_id, tax_unit_id, period)).fetchall()
        return format(sum((Decimal(r["amount"]) for r in rows), Decimal("0")), "f")

