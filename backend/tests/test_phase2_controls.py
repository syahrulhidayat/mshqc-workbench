from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import Settings
from app.db.repository import JobRow


class InMemoryControlRepo:
    def __init__(self) -> None:
        self._by_id: dict[str, JobRow] = {}
        self._by_hash: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def create_or_get_by_hash(
        self,
        *,
        input_hash: str,
        method: str,
        basis: str,
        charge: int,
        multiplicity: int,
        geometry_xyz: str,
    ) -> tuple[JobRow, bool]:
        if input_hash in self._by_hash:
            return self._by_id[self._by_hash[input_hash]], True
        now = datetime.now(UTC)
        row = JobRow(
            id=str(uuid.uuid4()),
            input_hash=input_hash,
            status="queued",
            method=method,
            basis=basis,
            charge=charge,
            multiplicity=multiplicity,
            geometry_xyz=geometry_xyz,
            engine=None,
            result_json=None,
            error_message=None,
            created_at=now,
            updated_at=now,
        )
        self._by_id[row.id] = row
        self._by_hash[input_hash] = row.id
        return row, False

    def get_by_id(self, job_id: str) -> JobRow | None:
        return self._by_id.get(job_id)

    def list_recent(self, limit: int = 25) -> list[JobRow]:
        return list(self._by_id.values())[:limit]

    def mark_cancelled(self, job_id: str) -> JobRow | None:
        row = self._by_id.get(job_id)
        if row is None or row.status not in {"queued", "running"}:
            return None
        updated = replace(row, status="cancelled", error_message="Cancelled by user.")
        self._by_id[job_id] = updated
        return updated

    def reset_for_retry(self, job_id: str) -> JobRow | None:
        row = self._by_id.get(job_id)
        if row is None or row.status not in {"failed", "cancelled"}:
            return None
        updated = replace(row, status="queued", engine=None, result_json=None, error_message=None)
        self._by_id[job_id] = updated
        return updated


class InMemoryControlRedis:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.published: list[tuple[str, str]] = []
        self.counters: dict[str, int] = {}

    async def ping(self) -> bool:
        return True

    async def lpush(self, key: str, value: str) -> int:
        self.pushed.append((key, value))
        return len(self.pushed)

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, _key: str, _seconds: int) -> bool:
        return True

    async def ttl(self, _key: str) -> int:
        return 45


def _test_settings(limit: int = 2) -> Settings:
    return Settings(
        database_url="postgresql://unused",
        redis_url="redis://unused",
        cors_origins=("http://localhost:5173",),
        solver_backend="surrogate",
        rate_limit_requests=limit,
        rate_limit_window_s=60,
    )


def test_rate_limiter_enforces_429_with_retry_after() -> None:
    app = create_app(settings=_test_settings(limit=2))
    app.state.repo = InMemoryControlRepo()
    app.state.redis = InMemoryControlRedis()

    payload = {
        "geometry_xyz": "O 0 0 0\nH 0.757 0.586 0\nH -0.757 0.586 0",
        "method": "RHF",
        "basis": "cc-pvdz",
    }

    with TestClient(app) as client:
        r1 = client.post("/api/v1/jobs", json=payload)
        assert r1.status_code == 201
        assert r1.headers["X-RateLimit-Remaining"] == "1"

        r2 = client.post("/api/v1/jobs", json=payload)
        assert r2.status_code == 200
        assert r2.headers["X-RateLimit-Remaining"] == "0"

        r3 = client.post("/api/v1/jobs", json=payload)
        assert r3.status_code == 429
        assert int(r3.headers["Retry-After"]) > 0


def test_cancel_and_retry_job_lifecycle() -> None:
    app = create_app(settings=_test_settings(limit=10))
    repo = InMemoryControlRepo()
    redis_mock = InMemoryControlRedis()
    app.state.repo = repo
    app.state.redis = redis_mock

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/jobs",
            json={
                "geometry_xyz": "O 0 0 0\nH 0.757 0.586 0\nH -0.757 0.586 0",
                "method": "RHF",
                "basis": "sto-3g",
            },
        )
        assert created.status_code == 201
        job_id = created.json()["id"]

        cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert len(redis_mock.published) == 1

        cancel_again = client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert cancel_again.status_code == 409

        retried = client.post(f"/api/v1/jobs/{job_id}/retry")
        assert retried.status_code == 200
        assert retried.json()["status"] == "queued"
        assert len(redis_mock.pushed) == 2
