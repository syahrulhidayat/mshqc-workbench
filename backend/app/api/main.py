from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.api.rate_limit import check_rate_limit
from app.api.schemas import CapabilitiesResponse, JobCreateRequest, JobResponse
from app.config import Settings, load_settings
from app.db.repository import JobRepository, JobRow
from app.domain.basis_catalog import normalize_and_validate_basis
from app.domain.geometry import parse_xyz, validate_spin_parity
from app.domain.hashing import canonical_job_hash
from app.domain.solver import active_engine_family, get_capabilities


def _serialize_job(row: JobRow, *, cache_hit: bool = False) -> JobResponse:
    return JobResponse(
        id=row.id,
        input_hash=row.input_hash,
        status=row.status,
        method=row.method,
        basis=row.basis,
        charge=row.charge,
        multiplicity=row.multiplicity,
        eri_method=row.eri_method,
        freeze_core=row.freeze_core,
        geometry_xyz=row.geometry_xyz,
        engine=row.engine,
        result=row.result_json,
        error_message=row.error_message,
        cache_hit=cache_hit,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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

    app = FastAPI(title="mshqc-workbench API", version="0.3.0", lifespan=lifespan)
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
            ri_basis_sets=list(caps.ri_basis_sets),
            eri_methods=list(caps.eri_methods),
            basis_dir=caps.basis_dir,
            discovered_basis_count=caps.discovered_basis_count,
        )

    @app.post("/api/v1/jobs", response_model=JobResponse)
    async def submit_job(
        payload: JobCreateRequest,
        request: Request,
        response: Response,
    ) -> JobResponse:
        rl = await check_rate_limit(
            request.app.state.redis,
            client_key=_client_ip(request),
            limit=cfg.rate_limit_requests,
            window_s=cfg.rate_limit_window_s,
        )
        if rl.limit > 0:
            response.headers["X-RateLimit-Limit"] = str(rl.limit)
            response.headers["X-RateLimit-Remaining"] = str(rl.remaining)
        if not rl.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Retry in {rl.retry_after_s}s.",
                headers={
                    "Retry-After": str(rl.retry_after_s),
                    "X-RateLimit-Limit": str(rl.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        try:
            atoms = parse_xyz(payload.geometry_xyz)
            validate_spin_parity(atoms, payload.charge, payload.multiplicity)
            canonical_basis = normalize_and_validate_basis(payload.basis)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

        normalized_xyz = "\n".join(
            f"{a.symbol} {a.x:.8f} {a.y:.8f} {a.z:.8f}" for a in atoms
        )
        engine_family = active_engine_family(cfg.solver_backend)
        input_hash = canonical_job_hash(
            atoms=atoms,
            method=payload.method,
            basis=canonical_basis,
            charge=payload.charge,
            multiplicity=payload.multiplicity,
            eri_method=payload.eri_method,
            freeze_core=payload.freeze_core,
            engine_family=engine_family,
        )

        row, cache_hit = await asyncio.to_thread(
            request.app.state.repo.create_or_get_by_hash,
            input_hash=input_hash,
            method=payload.method,
            basis=canonical_basis,
            charge=payload.charge,
            multiplicity=payload.multiplicity,
            eri_method=payload.eri_method,
            freeze_core=payload.freeze_core,
            geometry_xyz=normalized_xyz,
        )

        if not cache_hit:
            await request.app.state.redis.lpush(cfg.queue_key, row.id)
            response.status_code = status.HTTP_201_CREATED
        else:
            response.status_code = status.HTTP_200_OK

        return _serialize_job(row, cache_hit=cache_hit)

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobResponse)
    async def cancel_job(job_id: str, request: Request) -> JobResponse:
        existing = await asyncio.to_thread(request.app.state.repo.get_by_id, job_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Job not found.")

        updated = await asyncio.to_thread(request.app.state.repo.mark_cancelled, job_id)
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel job in '{existing.status}' state.",
            )

        channel = f"{cfg.event_channel_prefix}{job_id}"
        await request.app.state.redis.publish(
            channel,
            json.dumps(
                {
                    "type": "done",
                    "payload": {
                        "status": "cancelled",
                        "engine": None,
                        "result": None,
                        "error_message": updated.error_message,
                    },
                }
            ),
        )
        return _serialize_job(updated)

    @app.post("/api/v1/jobs/{job_id}/retry", response_model=JobResponse)
    async def retry_job(job_id: str, request: Request) -> JobResponse:
        existing = await asyncio.to_thread(request.app.state.repo.get_by_id, job_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Job not found.")

        requeued = await asyncio.to_thread(request.app.state.repo.reset_for_retry, job_id)
        if requeued is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Only failed or cancelled jobs can be retried "
                    f"(current: '{existing.status}')."
                ),
            )

        await request.app.state.redis.lpush(cfg.queue_key, requeued.id)
        return _serialize_job(requeued)

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
            pubsub = request.app.state.redis.pubsub()
            await pubsub.subscribe(channel)
            try:
                current = await asyncio.to_thread(request.app.state.repo.get_by_id, job_id)
                if current is None:
                    return

                if current.status in {"completed", "failed", "cancelled"}:
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
