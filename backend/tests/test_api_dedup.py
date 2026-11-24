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
        eri_method: str = "auto",
        freeze_core: bool = False,
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
            eri_method=eri_method,
            freeze_core=freeze_core,
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
        self.counters: dict[str, int] = {}

    async def ping(self) -> bool:
        return True

    async def lpush(self, key: str, value: str) -> int:
        self.pushed.append((key, value))
        return len(self.pushed)

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, _key: str, _seconds: int) -> bool:
        return True

    async def ttl(self, _key: str) -> int:
        return 60


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


def test_submit_job_separates_cache_on_eri_and_freeze_core() -> None:
    app = create_app()
    fake_repo = FakeRepo()
    fake_redis = FakeRedis()
    app.state.repo = fake_repo
    app.state.redis = fake_redis

    geom = "O 0 0 0\nH 0.757 0.586 0\nH -0.757 0.586 0"
    with TestClient(app) as client:
        r_df = client.post(
            "/api/v1/jobs",
            json={"geometry_xyz": geom, "method": "OMP2", "basis": "cc-pvdz", "eri_method": "df"},
        )
        r_exact = client.post(
            "/api/v1/jobs",
            json={
                "geometry_xyz": geom,
                "method": "OMP2",
                "basis": "cc-pvdz",
                "eri_method": "exact",
            },
        )
        r_fc = client.post(
            "/api/v1/jobs",
            json={
                "geometry_xyz": geom,
                "method": "OMP2",
                "basis": "cc-pvdz",
                "eri_method": "df",
                "freeze_core": True,
            },
        )
        assert r_df.status_code == 201
        assert r_exact.status_code == 201
        assert r_fc.status_code == 201
        assert len({r_df.json()["id"], r_exact.json()["id"], r_fc.json()["id"]}) == 3


def test_submit_job_rejects_invalid_geometry_and_basis() -> None:
    app = create_app()
    app.state.repo = FakeRepo()
    app.state.redis = FakeRedis()

    with TestClient(app) as client:
        bad_geom = client.post(
            "/api/v1/jobs",
            json={"geometry_xyz": "Xx 0 0 0", "method": "RHF", "basis": "sto-3g"},
        )
        assert bad_geom.status_code == 422

        bad_basis = client.post(
            "/api/v1/jobs",
            json={"geometry_xyz": "H 0 0 0\nH 0 0 0.74", "method": "RHF", "basis": "../etc/passwd"},
        )
        assert bad_basis.status_code == 422
