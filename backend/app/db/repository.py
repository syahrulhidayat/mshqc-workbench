from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@dataclass(frozen=True)
class JobRow:
    id: str
    input_hash: str
    status: str
    method: str
    basis: str
    charge: int
    multiplicity: int
    geometry_xyz: str
    engine: str | None
    result_json: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    eri_method: str = "auto"
    freeze_core: bool = False


class JobRepository:
    def __init__(self, database_url: str) -> None:
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    def close(self) -> None:
        self._pool.close()

    def init_schema(self) -> None:
        ddl = SCHEMA_PATH.read_text(encoding="utf-8")
        with self._pool.connection() as conn:
            conn.execute(ddl)
            conn.commit()

    def ping(self) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT 1 AS ok").fetchone()
            return bool(row and row["ok"] == 1)

    def create_or_get_by_hash(
        self,
        *,
        input_hash: str,
        method: str,
        basis: str,
        charge: int,
        multiplicity: int,
        geometry_xyz: str,
        eri_method: str = "auto",
        freeze_core: bool = False,
    ) -> tuple[JobRow, bool]:
        new_id = str(uuid.uuid4())
        insert_sql = (
            "INSERT INTO compute_jobs "
            "(id, input_hash, status, method, basis, charge, multiplicity, "
            "eri_method, freeze_core, geometry_xyz) "
            "VALUES (%s, %s, 'queued', %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (input_hash) DO NOTHING "
            "RETURNING *"
        )
        select_sql = "SELECT * FROM compute_jobs WHERE input_hash = %s"

        with self._pool.connection() as conn:
            inserted = conn.execute(
                insert_sql,
                (
                    new_id,
                    input_hash,
                    method,
                    basis,
                    charge,
                    multiplicity,
                    eri_method,
                    freeze_core,
                    geometry_xyz,
                ),
            ).fetchone()
            conn.commit()
            if inserted is not None:
                return _to_job_row(inserted), False

            existing = conn.execute(select_sql, (input_hash,)).fetchone()
            if existing is None:
                raise RuntimeError("Concurrent job lookup failed after hash conflict.")
            return _to_job_row(existing), True

    def get_by_id(self, job_id: str) -> JobRow | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM compute_jobs WHERE id = %s",
                (job_id,),
            ).fetchone()
            return _to_job_row(row) if row is not None else None

    def list_recent(self, limit: int = 25) -> list[JobRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM compute_jobs ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
            return [_to_job_row(r) for r in rows]

    def mark_running(self, job_id: str) -> bool:
        sql = (
            "UPDATE compute_jobs SET status = 'running', updated_at = NOW() "
            "WHERE id = %s AND status = 'queued' RETURNING id"
        )
        with self._pool.connection() as conn:
            row = conn.execute(sql, (job_id,)).fetchone()
            conn.commit()
            return row is not None

    def mark_completed(self, job_id: str, engine: str, result: dict[str, Any]) -> bool:
        sql = (
            "UPDATE compute_jobs "
            "SET status = 'completed', engine = %s, result_json = %s::jsonb, "
            "error_message = NULL, updated_at = NOW() "
            "WHERE id = %s AND status != 'cancelled' RETURNING id"
        )
        with self._pool.connection() as conn:
            row = conn.execute(sql, (engine, json.dumps(result), job_id)).fetchone()
            conn.commit()
            return row is not None

    def mark_failed(self, job_id: str, error_message: str) -> None:
        sql = (
            "UPDATE compute_jobs "
            "SET status = 'failed', error_message = %s, updated_at = NOW() "
            "WHERE id = %s AND status != 'cancelled'"
        )
        with self._pool.connection() as conn:
            conn.execute(sql, (error_message[:1000], job_id))
            conn.commit()

    def mark_cancelled(self, job_id: str) -> JobRow | None:
        sql = (
            "UPDATE compute_jobs "
            "SET status = 'cancelled', error_message = 'Cancelled by user.', updated_at = NOW() "
            "WHERE id = %s AND status IN ('queued', 'running') RETURNING *"
        )
        with self._pool.connection() as conn:
            row = conn.execute(sql, (job_id,)).fetchone()
            conn.commit()
            return _to_job_row(row) if row is not None else None

    def reset_for_retry(self, job_id: str) -> JobRow | None:
        sql = (
            "UPDATE compute_jobs "
            "SET status = 'queued', engine = NULL, result_json = NULL, "
            "error_message = NULL, updated_at = NOW() "
            "WHERE id = %s AND status IN ('failed', 'cancelled') RETURNING *"
        )
        with self._pool.connection() as conn:
            row = conn.execute(sql, (job_id,)).fetchone()
            conn.commit()
            return _to_job_row(row) if row is not None else None


def _to_job_row(raw: dict[str, Any]) -> JobRow:
    return JobRow(
        id=str(raw["id"]),
        input_hash=str(raw["input_hash"]).strip(),
        status=str(raw["status"]),
        method=str(raw["method"]),
        basis=str(raw["basis"]),
        charge=int(raw["charge"]),
        multiplicity=int(raw["multiplicity"]),
        geometry_xyz=str(raw["geometry_xyz"]),
        engine=raw["engine"],
        result_json=raw["result_json"],
        error_message=raw["error_message"],
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
        eri_method=str(raw.get("eri_method") or "auto"),
        freeze_core=bool(raw.get("freeze_core", False)),
    )
