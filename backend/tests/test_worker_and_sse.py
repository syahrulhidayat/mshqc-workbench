from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.db.repository import JobRow
from app.worker.runner import process_job


def _make_job(job_id: str, status: str = "queued", result: dict[str, Any] | None = None) -> JobRow:
    now = datetime.now(UTC)
    return JobRow(
        id=job_id,
        input_hash="a" * 64,
        status=status,
        method="RHF",
        basis="cc-pvdz",
        charge=0,
        multiplicity=1,
        geometry_xyz="O 0.0 0.0 0.0\nH 0.757 0.586 0.0\nH -0.757 0.586 0.0",
        engine="surrogate-deterministic" if result else None,
        result_json=result,
        error_message=None,
        created_at=now,
        updated_at=now,
    )


class WorkerStubRepo:
    def __init__(self, row: JobRow) -> None:
        self.row = row

    def get_by_id(self, job_id: str) -> JobRow | None:
        return self.row if self.row.id == job_id else None

    def mark_running(self, job_id: str) -> bool:
        if self.row.id != job_id or self.row.status != "queued":
            return False
        self.row = replace(self.row, status="running")
        return True

    def mark_completed(self, job_id: str, engine: str, result: dict[str, Any]) -> bool:
        if self.row.id != job_id or self.row.status == "cancelled":
            return False
        self.row = replace(self.row, status="completed", engine=engine, result_json=result)
        return True

    def mark_failed(self, job_id: str, error_message: str) -> None:
        if self.row.id == job_id and self.row.status != "cancelled":
            self.row = replace(self.row, status="failed", error_message=error_message)


class SyncRedisStub:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []

    def publish(self, channel: str, payload: str) -> int:
        self.messages.append((channel, json.loads(payload)))
        return 1


class AsyncPubSubStub:
    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, _channel: str) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True


class AsyncRedisStub:
    def __init__(self) -> None:
        self.last_pubsub: AsyncPubSubStub | None = None

    async def ping(self) -> bool:
        return True

    def pubsub(self) -> AsyncPubSubStub:
        self.last_pubsub = AsyncPubSubStub()
        return self.last_pubsub


def test_worker_processes_queued_job_and_publishes_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    repo = WorkerStubRepo(_make_job("job-1", status="queued"))
    redis_stub = SyncRedisStub()

    process_job(
        "job-1",
        repo,  # type: ignore[arg-type]
        redis_stub,  # type: ignore[arg-type]
        channel_prefix="mshqc:events:",
        solver_backend="surrogate",
    )

    assert repo.row.status == "completed"
    assert repo.row.engine == "surrogate-deterministic"
    assert repo.row.result_json is not None
    event_types = [msg["type"] for _, msg in redis_stub.messages]
    assert event_types[-1] == "done"
    assert event_types.count("iteration") >= 5


def test_worker_skips_cancelled_job_without_running_solver() -> None:
    repo = WorkerStubRepo(_make_job("job-2", status="cancelled"))
    redis_stub = SyncRedisStub()

    process_job(
        "job-2",
        repo,  # type: ignore[arg-type]
        redis_stub,  # type: ignore[arg-type]
        channel_prefix="mshqc:events:",
        solver_backend="surrogate",
    )

    assert repo.row.status == "cancelled"
    assert redis_stub.messages == []


def test_sse_stream_replays_completed_job_and_closes_pubsub() -> None:
    completed_result = {
        "engine": "surrogate-deterministic",
        "method": "RHF",
        "basis": "cc-pvdz",
        "nuclear_repulsion_hartree": 9.19,
        "total_energy_hartree": -76.02,
        "converged": True,
        "iterations": [
            {"iteration": 1, "energy_hartree": -75.5, "delta_e": -0.5, "elapsed_ms": 5.0},
            {"iteration": 2, "energy_hartree": -76.02, "delta_e": -0.52, "elapsed_ms": 10.0},
        ],
        "wall_time_ms": 10.0,
    }
    app = create_app()
    app.state.repo = WorkerStubRepo(_make_job("job-3", status="completed", result=completed_result))
    redis_stub = AsyncRedisStub()
    app.state.redis = redis_stub

    with TestClient(app) as client:
        resp = client.get("/api/v1/jobs/job-3/events")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert "event: iteration" in resp.text
        assert "event: done" in resp.text
        assert redis_stub.last_pubsub is not None
        assert redis_stub.last_pubsub.closed is True
