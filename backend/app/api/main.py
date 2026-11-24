from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.api.schemas import CapabilitiesResponse, JobCreateRequest, JobResponse
from app.config import Settings, load_settings
from app.db.repository import JobRepository, JobRow
from app.domain.geometry import parse_xyz, validate_spin_parity
from app.domain.hashing import canonical_job_hash
from app.domain.solver import get_capabilities


def _serialize_job(row: JobRow, *, cache_hit: bool = False) -> JobResponse:
    return JobResponse(
        id=row.id,
        input_hash=row.input_hash,
        status=row.status,
        method=row.method,
        basis=row.basis,
        charge=row.charge,
        multiplicity=row.multiplicity,
        geometry_xyz=row.geometry_xyz,
        engine=row.engine,
        result=row.result_json,
        error_message=row.error_message,
        cache_hit=cache_hit,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if not hasattr(application.state, "repo"):
            repo = JobRepository(cfg.database_url)
            repo.init_schema()
            application.state.repo = repo
            application.state.owns_repo = True
        else:
            application.state.owns_repo = False

        if not hasattr(application.state, "redis"):
            application.state.redis = aioredis.from_url(cfg.redis_url, decode_responses=True)
            application.state.owns_redis = True
        else:
            application.state.owns_redis = False

        try:
            yield
        finally:
            if application.state.owns_redis:
                await application.state.redis.aclose()
            if application.state.owns_repo:
                application.state.repo.close()

    app = FastAPI(title="mshqc-workbench API", version="0.1.0", lifespan=lifespan)
    app.state.settings = cfg

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, str]:
        db_ok = await asyncio.to_thread(request.app.state.repo.ping)
        redis_ok = bool(await request.app.state.redis.ping())
        if not (db_ok and redis_ok):
            raise HTTPException(status_code=503, detail="Backing service unavailable.")
        return {"status": "ok"}

    @app.get("/api/v1/capabilities", response_model=CapabilitiesResponse)
    async def capabilities() -> CapabilitiesResponse:
        caps = get_capabilities(cfg.solver_backend)
        return CapabilitiesResponse(
            active_engine=caps.active_engine,
            mshqc_installed=caps.mshqc_installed,
            mshqc_version=caps.mshqc_version,
            methods=list(caps.methods),
            basis_sets=list(caps.basis_sets),
        )

    @app.post("/api/v1/jobs", response_model=JobResponse)
    async def submit_job(
        payload: JobCreateRequest,
        request: Request,
        response: Response,
    ) -> JobResponse:
        try:
            atoms = parse_xyz(payload.geometry_xyz)
            validate_spin_parity(atoms, payload.charge, payload.multiplicity)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

        normalized_xyz = "\n".join(
            f"{a.symbol} {a.x:.8f} {a.y:.8f} {a.z:.8f}" for a in atoms
        )
        input_hash = canonical_job_hash(
            atoms=atoms,
            method=payload.method,
            basis=payload.basis,
            charge=payload.charge,
            multiplicity=payload.multiplicity,
        )

        row, cache_hit = await asyncio.to_thread(
            request.app.state.repo.create_or_get_by_hash,
            input_hash=input_hash,
            method=payload.method,
            basis=payload.basis,
            charge=payload.charge,
            multiplicity=payload.multiplicity,
            geometry_xyz=normalized_xyz,
        )

        if not cache_hit:
            await request.app.state.redis.lpush(cfg.queue_key, row.id)
            response.status_code = status.HTTP_201_CREATED
        else:
            response.status_code = status.HTTP_200_OK

        return _serialize_job(row, cache_hit=cache_hit)

    @app.get("/api/v1/jobs", response_model=list[JobResponse])
    async def list_jobs(request: Request, limit: int = 25) -> list[JobResponse]:
        clamped = max(1, min(limit, 100))
        rows = await asyncio.to_thread(request.app.state.repo.list_recent, clamped)
        return [_serialize_job(r) for r in rows]

    @app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
    async def get_job(job_id: str, request: Request) -> JobResponse:
        row = await asyncio.to_thread(request.app.state.repo.get_by_id, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return _serialize_job(row)

    @app.get("/api/v1/jobs/{job_id}/events")
    async def stream_job_events(job_id: str, request: Request) -> StreamingResponse:
        row = await asyncio.to_thread(request.app.state.repo.get_by_id, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found.")

        channel = f"{cfg.event_channel_prefix}{job_id}"

        async def event_stream() -> AsyncIterator[str]:
            # Subscribe before re-reading DB status to prevent missing terminal events.
            pubsub = request.app.state.redis.pubsub()
            await pubsub.subscribe(channel)
            try:
                current = await asyncio.to_thread(request.app.state.repo.get_by_id, job_id)
                if current is None:
                    return

                if current.status in {"completed", "failed"}:
                    if current.result_json:
                        for item in current.result_json.get("iterations", []):
                            yield f"event: iteration\ndata: {json.dumps(item)}\n\n"
                    terminal = {
                        "status": current.status,
                        "engine": current.engine,
                        "result": current.result_json,
                        "error_message": current.error_message,
                    }
                    yield f"event: done\ndata: {json.dumps(terminal)}\n\n"
                    return

                while True:
                    if await request.is_disconnected():
                        break
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg is None:
                        yield ": keepalive\n\n"
                        continue

                    parsed = json.loads(msg["data"])
                    event_type = parsed.get("type", "iteration")
                    yield f"event: {event_type}\ndata: {json.dumps(parsed['payload'])}\n\n"
                    if event_type == "done":
                        break
            finally:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
