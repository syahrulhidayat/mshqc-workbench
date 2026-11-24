from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from app.domain.geometry import ATOMIC_NUMBERS, Atom
from app.domain.mshqc_adapter import (
    IterationRecord,
    SolveResult,
    detect_mshqc_version,
    run_mshqc,
)

__all__ = [
    "IterationRecord",
    "SolveResult",
    "SolverCapabilities",
    "SUPPORTED_BASIS_SETS",
    "SUPPORTED_METHODS",
    "execute_solver",
    "get_capabilities",
    "nuclear_repulsion_energy",
]

BOHR_PER_ANGSTROM = 1.8897261246257702
SUPPORTED_METHODS: tuple[str, ...] = ("RHF", "UHF", "RMP2", "UMP2", "OMP2", "OMP3")
SUPPORTED_BASIS_SETS: tuple[str, ...] = ("sto-3g", "6-31g", "cc-pvdz", "cc-pvtz")


@dataclass(frozen=True)
class SolverCapabilities:
    active_engine: str
    mshqc_installed: bool
    mshqc_version: str | None
    methods: tuple[str, ...]
    basis_sets: tuple[str, ...]


def get_capabilities(backend_preference: str = "auto") -> SolverCapabilities:
    version = detect_mshqc_version()
    installed = version is not None
    if backend_preference == "surrogate":
        active = "surrogate-deterministic"
    elif installed:
        active = f"mshqc-{version}"
    elif backend_preference == "mshqc":
        active = "mshqc-unavailable"
    else:
        active = "surrogate-deterministic"

    return SolverCapabilities(
        active_engine=active,
        mshqc_installed=installed,
        mshqc_version=version,
        methods=SUPPORTED_METHODS,
        basis_sets=SUPPORTED_BASIS_SETS,
    )


def nuclear_repulsion_energy(atoms: tuple[Atom, ...]) -> float:
    energy = 0.0
    for i in range(len(atoms)):
        zi = ATOMIC_NUMBERS[atoms[i].symbol]
        for j in range(i + 1, len(atoms)):
            zj = ATOMIC_NUMBERS[atoms[j].symbol]
            dist_ang = math.dist(
                (atoms[i].x, atoms[i].y, atoms[i].z),
                (atoms[j].x, atoms[j].y, atoms[j].z),
            )
            energy += (zi * zj) / (dist_ang * BOHR_PER_ANGSTROM)
    return energy


def _run_deterministic_surrogate(
    atoms: tuple[Atom, ...],
    method: str,
    basis: str,
    on_iteration: Callable[[IterationRecord], None] | None,
    step_delay_s: float = 0.08,
) -> SolveResult:
    # Deterministic SCF-like convergence surrogate used when mshqc wheel is not installed.
    t0 = time.perf_counter()
    e_nuc = nuclear_repulsion_energy(atoms)
    electronic_scale = sum(-(ATOMIC_NUMBERS[a.symbol] ** 2.15) * 0.62 for a in atoms)
    method_shift = {
        "RHF": 0.0,
        "UHF": -0.015,
        "RMP2": -0.214,
        "UMP2": -0.219,
        "OMP2": -0.228,
        "OMP3": -0.241,
    }.get(method, -0.05)
    basis_shift = {
        "sto-3g": 0.45,
        "6-31g": 0.12,
        "cc-pvdz": 0.0,
        "cc-pvtz": -0.045,
    }.get(basis.lower(), 0.05)
    target_energy = electronic_scale + e_nuc + method_shift + basis_shift

    history: list[dict[str, float | int]] = []
    prev_e = target_energy + 1.25
    for step in range(1, 11):
        if step_delay_s > 0:
            time.sleep(step_delay_s)
        residual = 1.25 * (0.28 ** step)
        current_e = target_energy + residual
        delta_e = current_e - prev_e
        prev_e = current_e
        elapsed = (time.perf_counter() - t0) * 1000.0
        rec = IterationRecord(
            iteration=step,
            energy_hartree=round(current_e, 10),
            delta_e=round(delta_e, 10),
            elapsed_ms=round(elapsed, 2),
        )
        history.append(asdict(rec))
        if on_iteration is not None:
            on_iteration(rec)
        if abs(delta_e) < 1e-6:
            break

    wall_ms = (time.perf_counter() - t0) * 1000.0
    return SolveResult(
        engine="surrogate-deterministic",
        method=method,
        basis=basis,
        nuclear_repulsion_hartree=round(e_nuc, 10),
        total_energy_hartree=float(history[-1]["energy_hartree"]),
        converged=True,
        iterations=history,
        wall_time_ms=round(wall_ms, 2),
    )


def execute_solver(
    *,
    atoms: tuple[Atom, ...],
    method: str,
    basis: str,
    charge: int,
    multiplicity: int,
    backend_preference: str = "auto",
    on_iteration: Callable[[IterationRecord], None] | None = None,
    step_delay_s: float = 0.08,
) -> SolveResult:
    if backend_preference in {"auto", "mshqc"}:
        e_nuc = nuclear_repulsion_energy(atoms)
        result = run_mshqc(
            atoms=atoms,
            method=method,
            basis=basis,
            charge=charge,
            multiplicity=multiplicity,
            nuclear_repulsion_hartree=e_nuc,
            on_iteration=on_iteration,
        )
        if result is not None:
            return result
        if backend_preference == "mshqc":
            raise RuntimeError(
                "SOLVER_BACKEND=mshqc requested, but 'mshqc' package is not installed."
            )

    return _run_deterministic_surrogate(
        atoms=atoms,
        method=method,
        basis=basis,
        on_iteration=on_iteration,
        step_delay_s=step_delay_s,
    )
