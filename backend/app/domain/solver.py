from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from app.domain.basis_catalog import CURATED_BASIS_SETS, discover_basis_catalog
from app.domain.geometry import ATOMIC_NUMBERS, Atom
from app.domain.mshqc_adapter import (
    IterationRecord,
    SolveResult,
    detect_mshqc_engine_label,
    detect_mshqc_version,
    run_mshqc,
)

__all__ = [
    "IterationRecord",
    "SolveResult",
    "SolverCapabilities",
    "SUPPORTED_BASIS_SETS",
    "SUPPORTED_ERI_METHODS",
    "SUPPORTED_METHODS",
    "active_engine_family",
    "execute_solver",
    "get_capabilities",
    "nuclear_repulsion_energy",
]

BOHR_PER_ANGSTROM = 1.8897261246257702
SUPPORTED_METHODS: tuple[str, ...] = (
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
)
SUPPORTED_BASIS_SETS: tuple[str, ...] = CURATED_BASIS_SETS
SUPPORTED_ERI_METHODS: tuple[str, ...] = ("auto", "df", "cholesky", "exact")


@dataclass(frozen=True)
class SolverCapabilities:
    active_engine: str
    mshqc_installed: bool
    mshqc_version: str | None
    methods: tuple[str, ...]
    basis_sets: tuple[str, ...]
    ri_basis_sets: tuple[str, ...]
    eri_methods: tuple[str, ...]
    basis_dir: str | None
    discovered_basis_count: int


def get_capabilities(backend_preference: str = "auto") -> SolverCapabilities:
    version = detect_mshqc_version()
    engine_label = detect_mshqc_engine_label()
    installed = version is not None
    if backend_preference == "surrogate":
        active = "surrogate-deterministic"
    elif installed and engine_label is not None:
        active = engine_label
    elif backend_preference == "mshqc":
        active = "mshqc-unavailable"
    else:
        active = "surrogate-deterministic"

    catalog = discover_basis_catalog()
    return SolverCapabilities(
        active_engine=active,
        mshqc_installed=installed,
        mshqc_version=version,
        methods=SUPPORTED_METHODS,
        basis_sets=catalog.basis_sets,
        ri_basis_sets=catalog.ri_supported,
        eri_methods=SUPPORTED_ERI_METHODS,
        basis_dir=catalog.basis_dir,
        discovered_basis_count=catalog.discovered_count,
    )


def active_engine_family(backend_preference: str = "auto") -> str:
    caps = get_capabilities(backend_preference)
    return "mshqc" if caps.active_engine.startswith("mshqc") else "surrogate"


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
    multiplicity: int,
    eri_method: str,
    freeze_core: bool,
    on_iteration: Callable[[IterationRecord], None] | None,
    step_delay_s: float = 0.06,
) -> SolveResult:
    # Deterministic SCF/OMP-like surrogate used when mshqc is not installed.
    t0 = time.perf_counter()
    e_nuc = nuclear_repulsion_energy(atoms)
    electronic_scale = sum(-(ATOMIC_NUMBERS[a.symbol] ** 2.15) * 0.62 for a in atoms)
    basis_shift = {
        "sto-3g": 0.45,
        "sto-6g": 0.36,
        "3-21g": 0.24,
        "6-31g": 0.12,
        "6-31g_d_": 0.06,
        "6-31g_d_p_": 0.04,
        "cc-pvdz": 0.0,
        "cc-pvtz": -0.045,
        "cc-pvqz": -0.068,
        "def2-svp": 0.01,
        "def2-tzvp": -0.042,
    }.get(basis.lower(), -0.01)

    corr_shift = {
        "RHF": 0.0,
        "UHF": -0.015,
        "ROHF": -0.008,
        "RMP2": -0.214,
        "UMP2": -0.219,
        "ROMP2": -0.216,
        "RMP3": -0.232,
        "UMP3": -0.236,
        "ROMP3": -0.234,
        "OMP2": -0.228,
        "SCS-OMP2": -0.221,
        "SOS-OMP2": -0.218,
        "LT-SOS-OMP2": -0.218,
        "OMP2.5": -0.235,
        "OMP3": -0.241,
        "KAPPA-OMP2": -0.225,
    }.get(method, -0.05)

    n_frozen = sum(1 for a in atoms if ATOMIC_NUMBERS[a.symbol] >= 3) if freeze_core else 0
    if freeze_core and corr_shift != 0.0:
        corr_shift *= 0.94

    e_scf_target = electronic_scale + e_nuc + basis_shift
    is_omp = "OMP" in method
    is_mp = method in ("RMP2", "UMP2", "ROMP2", "RMP3", "UMP3", "ROMP3")

    history: list[dict[str, float | int | str]] = []
    prev_e = e_scf_target + 1.25
    step_idx = 0

    for scf_step in range(1, 7):
        step_idx += 1
        if step_delay_s > 0:
            time.sleep(step_delay_s)
        current_e = e_scf_target + 1.25 * (0.24**scf_step)
        delta_e = current_e - prev_e
        prev_e = current_e
        rec = IterationRecord(
            iteration=step_idx,
            energy_hartree=round(current_e, 10),
            delta_e=round(delta_e, 10),
            elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            stage="scf",
        )
        history.append(asdict(rec))
        if on_iteration is not None:
            on_iteration(rec)

    e_scf_final = float(history[-1]["energy_hartree"])
    e_total_final = e_scf_final

    if is_omp:
        target_total = e_scf_final + corr_shift
        for omp_step in range(1, 5):
            step_idx += 1
            if step_delay_s > 0:
                time.sleep(step_delay_s)
            current_e = target_total + abs(corr_shift) * (0.22**omp_step)
            delta_e = current_e - prev_e
            prev_e = current_e
            rec = IterationRecord(
                iteration=step_idx,
                energy_hartree=round(current_e, 10),
                delta_e=round(delta_e, 10),
                elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                stage="omp",
            )
            history.append(asdict(rec))
            if on_iteration is not None:
                on_iteration(rec)
        e_total_final = float(history[-1]["energy_hartree"])
    elif is_mp:
        step_idx += 1
        if step_delay_s > 0:
            time.sleep(step_delay_s)
        e_total_final = e_scf_final + corr_shift
        rec = IterationRecord(
            iteration=step_idx,
            energy_hartree=round(e_total_final, 10),
            delta_e=round(e_total_final - prev_e, 10),
            elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            stage="mp3" if "MP3" in method else "mp2",
        )
        history.append(asdict(rec))
        if on_iteration is not None:
            on_iteration(rec)

    s_spin = 0.5 * max(0, multiplicity - 1)
    s2 = round(s_spin * (s_spin + 1.0), 6)
    n_bf_est = sum(5 if ATOMIC_NUMBERS[a.symbol] <= 2 else 14 for a in atoms)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    e_corr_final = round(e_total_final - e_scf_final, 10)

    return SolveResult(
        engine="surrogate-deterministic",
        method=method,
        basis=basis,
        nuclear_repulsion_hartree=round(e_nuc, 10),
        total_energy_hartree=round(e_total_final, 10),
        converged=True,
        iterations=history,
        wall_time_ms=round(wall_ms, 2),
        energy_scf_hartree=round(e_scf_final, 10),
        energy_corr_hartree=e_corr_final,
        energy_mp2_hartree=e_corr_final if ("MP2" in method or "OMP" in method) else None,
        energy_mp3_hartree=round(e_corr_final * 0.12, 10) if "3" in method else None,
        s2=s2,
        n_basis_functions=n_bf_est,
        eri_method="surrogate" if eri_method == "auto" else eri_method,
        n_frozen=n_frozen,
    )


def execute_solver(
    *,
    atoms: tuple[Atom, ...],
    method: str,
    basis: str,
    charge: int,
    multiplicity: int,
    eri_method: str = "auto",
    freeze_core: bool = False,
    backend_preference: str = "auto",
    on_iteration: Callable[[IterationRecord], None] | None = None,
    step_delay_s: float = 0.06,
) -> SolveResult:
    if backend_preference in {"auto", "mshqc"}:
        e_nuc = nuclear_repulsion_energy(atoms)
        result = run_mshqc(
            atoms=atoms,
            method=method,
            basis=basis,
            charge=charge,
            multiplicity=multiplicity,
            eri_method=eri_method,
            freeze_core=freeze_core,
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
        multiplicity=multiplicity,
        eri_method=eri_method,
        freeze_core=freeze_core,
        on_iteration=on_iteration,
        step_delay_s=step_delay_s,
    )
