from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.db.repository import JobRow


class FakeRepo:
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


class FakeRedis:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    async def ping(self) -> bool:
        return True

    async def lpush(self, key: str, value: str) -> int:
        self.pushed.append((key, value))
        return len(self.pushed)


def test_submit_job_deduplicates_equivalent_geometries() -> None:
    app = create_app()
    fake_repo = FakeRepo()
    fake_redis = FakeRedis()
    app.state.repo = fake_repo
    app.state.redis = fake_redis

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/jobs",
            json={
                "geometry_xyz": "O 0 0 0\nH 0.757 0.586 0\nH -0.757 0.586 0",
                "method": "RHF",
                "basis": "cc-pvdz",
            },
        )
        assert first.status_code == 201
        first_body = first.json()
        assert first_body["cache_hit"] is False

        second = client.post(
            "/api/v1/jobs",
            json={
                "geometry_xyz": "O 0.0000 0.0 0.0\nH 0.7570 0.586 0.00\nH -0.7570 0.586 0",
                "method": "RHF",
                "basis": "cc-pvdz",
            },
        )
        assert second.status_code == 200
        second_body = second.json()
        assert second_body["cache_hit"] is True
        assert second_body["id"] == first_body["id"]
        assert len(fake_redis.pushed) == 1


def test_submit_job_rejects_invalid_geometry() -> None:
    app = create_app()
    app.state.repo = FakeRepo()
    app.state.redis = FakeRedis()

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/jobs",
            json={"geometry_xyz": "Xx 0 0 0", "method": "RHF", "basis": "sto-3g"},
        )
        assert resp.status_code == 422
