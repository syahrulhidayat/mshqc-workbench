from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AllowedMethod = Literal[
    "RHF",
    "UHF",
    "ROHF",
    "RMP2",
    "UMP2",
    "ROMP2",
    "RMP3",
    "UMP3",
    "ROMP3",
    "OMP2",
    "SCS-OMP2",
    "SOS-OMP2",
    "LT-SOS-OMP2",
    "OMP2.5",
    "OMP3",
    "KAPPA-OMP2",
]
AllowedEriMethod = Literal["auto", "df", "cholesky", "exact"]


class JobCreateRequest(BaseModel):
    geometry_xyz: str = Field(..., min_length=5, max_length=16000)
    method: AllowedMethod = "RHF"
    basis: str = Field(default="cc-pvdz", min_length=2, max_length=64)
    charge: int = Field(default=0, ge=-4, le=4)
    multiplicity: int = Field(default=1, ge=1, le=7)
    eri_method: AllowedEriMethod = "auto"
    freeze_core: bool = False


class JobResponse(BaseModel):
    id: str
    input_hash: str
    status: str
    method: str
    basis: str
    charge: int
    multiplicity: int
    eri_method: str = "auto"
    freeze_core: bool = False
    geometry_xyz: str
    engine: str | None = None
    result: dict[str, Any] | None = None
    error_message: str | None = None
    cache_hit: bool = False
    created_at: datetime
    updated_at: datetime


class CapabilitiesResponse(BaseModel):
    active_engine: str
    mshqc_installed: bool
    mshqc_version: str | None = None
    methods: list[str]
    basis_sets: list[str]
    ri_basis_sets: list[str]
    eri_methods: list[str]
    basis_dir: str | None = None
    discovered_basis_count: int = 0
