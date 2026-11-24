from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AllowedMethod = Literal["RHF", "RMP2", "OMP2"]
AllowedBasis = Literal["sto-3g", "6-31g", "cc-pvdz"]


class JobCreateRequest(BaseModel):
    geometry_xyz: str = Field(..., min_length=5, max_length=16000)
    method: AllowedMethod = "RHF"
    basis: AllowedBasis = "cc-pvdz"
    charge: int = Field(default=0, ge=-4, le=4)
    multiplicity: int = Field(default=1, ge=1, le=5)


class JobResponse(BaseModel):
    id: str
    input_hash: str
    status: str
    method: str
    basis: str
    charge: int
    multiplicity: int
    geometry_xyz: str
    engine: str | None = None
    result: dict[str, Any] | None = None
    error_message: str | None = None
    cache_hit: bool = False
    created_at: datetime
    updated_at: datetime
