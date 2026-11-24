from __future__ import annotations

import json
import logging
import signal
from dataclasses import asdict

import redis

from app.config import load_settings
from app.db.repository import JobRepository
from app.domain.geometry import parse_xyz
from app.domain.solver import IterationRecord, execute_solver

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mshqc.worker")

_SHOULD_STOP = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _SHOULD_STOP
    logger.info("Received signal %s, finishing active job before shutdown.", signum)
    _SHOULD_STOP = True


def process_job(
    job_id: str,
    repo: JobRepository,
    redis_client: redis.Redis,
    *,
    channel_prefix: str,
    solver_backend: str,
) -> None:
    job = repo.get_by_id(job_id)
    if job is None:
        logger.warning("Job %s missing in database, skipping.", job_id)
        return
    if job.status != "queued":
        logger.info("Skipping job %s in non-queued state '%s'.", job_id, job.status)
        return

    if not repo.mark_running(job_id):
        logger.info("Job %s transitioned before worker lock, skipping.", job_id)
        return

    channel = f"{channel_prefix}{job_id}"

    def publish_iteration(rec: IterationRecord) -> None:
        envelope = {"type": "iteration", "payload": asdict(rec)}
        redis_client.publish(channel, json.dumps(envelope))

    try:
        atoms = parse_xyz(job.geometry_xyz)
        result = execute_solver(
            atoms=atoms,
            method=job.method,
            basis=job.basis,
            charge=job.charge,
            multiplicity=job.multiplicity,
            backend_preference=solver_backend,
            on_iteration=publish_iteration,
        )
        result_dict = asdict(result)
        committed = repo.mark_completed(job_id, result.engine, result_dict)
        if not committed:
            logger.info("Job %s was cancelled during compute; discarding result.", job_id)
            return

        redis_client.publish(
            channel,
            json.dumps(
                {
                    "type": "done",
                    "payload": {
                        "status": "completed",
                        "engine": result.engine,
                        "result": result_dict,
                        "error_message": None,
                    },
                }
            ),
        )
        logger.info(
            "Completed job %s via %s in %.2f ms.",
            job_id,
            result.engine,
            result.wall_time_ms,
        )
    except Exception as exc:
        logger.exception("Job %s failed.", job_id)
        repo.mark_failed(job_id, str(exc))
        redis_client.publish(
            channel,
            json.dumps(
                {
                    "type": "done",
                    "payload": {
                        "status": "failed",
                        "engine": None,
                        "result": None,
                        "error_message": str(exc),
                    },
                }
            ),
        )


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    settings = load_settings()
    repo = JobRepository(settings.database_url)
    repo.init_schema()
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    logger.info(
        "Worker online. Queue='%s' backend=%s.",
        settings.queue_key,
        settings.solver_backend,
    )
    try:
        while not _SHOULD_STOP:
            popped = redis_client.brpop(settings.queue_key, timeout=2)
            if popped is None:
                continue
            _, job_id = popped
            process_job(
                job_id,
                repo,
                redis_client,
                channel_prefix=settings.event_channel_prefix,
                solver_backend=settings.solver_backend,
            )
    finally:
        redis_client.close()
        repo.close()
        logger.info("Worker stopped cleanly.")


if __name__ == "__main__":
    main()
