from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    cors_origins: tuple[str, ...]
    solver_backend: str
    queue_key: str = "mshqc:queue"
    event_channel_prefix: str = "mshqc:events:"


def load_settings() -> Settings:
    raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    origins = tuple(o.strip() for o in raw_origins.split(",") if o.strip())
    return Settings(
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql://workbench:workbench_dev_pass@localhost:5432/mshqc_workbench",
        ),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        cors_origins=origins,
        solver_backend=os.getenv("SOLVER_BACKEND", "auto").strip().lower(),
    )
