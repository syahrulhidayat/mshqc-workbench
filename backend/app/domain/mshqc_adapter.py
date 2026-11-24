from __future__ import annotations

import contextlib
import importlib.metadata
import inspect
import io
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from app.domain.geometry import Atom

_KV_ITER_RE = re.compile(
    r"(?:iter(?:ation)?\s*[=:#]?\s*(\d+)).*?"
    r"(?:\bE(?:nergy)?\s*[=:]\s*([-+]?\d+\.\d+(?:[eE][-+]?\d+)?))"
    r"(?:.*?(?:\bdE\s*[=:]\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)))?",
    re.IGNORECASE,
)

_TABULAR_ITER_RE = re.compile(
    r"^\s*(?:@?[A-Z0-9]{2,6}\s+)?(\d{1,3})\s+"
    r"([-+]?\d+\.\d{4,})\s+"
    r"([-+]?\d+(?:\.\d+)?[eE][-+]?\d+|[-+]?\d+\.\d{4,})\b"
)

_BASIS_CANONICAL: dict[str, str] = {
    "sto-3g": "STO-3G",
    "6-31g": "6-31G",
    "cc-pvdz": "cc-pVDZ",
    "cc-pvtz": "cc-pVTZ",
}

_OMP_STANDARD_PARAMS: dict[str, tuple[str, float, float, float]] = {
    "omp2": ("omp2", 1.0, 1.0, 0.0),
    "scs-omp2": ("scs-omp2", 1.2, 1.0 / 3.0, 0.0),
    "sos-omp2": ("sos-omp2", 1.3, 0.0, 0.0),
    "omp2.5": ("omp2.5", 1.0, 1.0, 0.5),
    "omp3": ("omp3", 1.0, 1.0, 1.0),
}


@dataclass(frozen=True)
class IterationRecord:
    iteration: int
    energy_hartree: float
    delta_e: float
    elapsed_ms: float


@dataclass(frozen=True)
class SolveResult:
    engine: str
    method: str
    basis: str
    nuclear_repulsion_hartree: float
    total_energy_hartree: float
    converged: bool
    iterations: list[dict[str, float | int]]
    wall_time_ms: float


def detect_mshqc_version() -> str | None:
    try:
        import mshqc  # type: ignore[import-not-found]
    except ImportError:
        return None

    version = getattr(mshqc, "__version__", None)
    if isinstance(version, str) and version.strip():
        return version.strip()

    try:
        return importlib.metadata.version("mshqc")
    except importlib.metadata.PackageNotFoundError:
        return "installed"


def is_hybrid_v2_engine(mshqc_mod: Any) -> bool:
    return hasattr(mshqc_mod, "MSHQCCalculator") and hasattr(mshqc_mod, "methods")


def detect_mshqc_engine_label() -> str | None:
    version = detect_mshqc_version()
    if version is None:
        return None
    try:
        import mshqc  # type: ignore[import-not-found]
    except ImportError:
        return None
    if is_hybrid_v2_engine(mshqc):
        return f"mshqc-{version}-hybrid-v2"
    return f"mshqc-{version}"


def parse_iteration_line(line: str, *, elapsed_ms: float) -> IterationRecord | None:
    match = _KV_ITER_RE.search(line)
    if match is not None:
        step = int(match.group(1))
        energy = float(match.group(2))
        delta_e = float(match.group(3)) if match.group(3) is not None else 0.0
        return IterationRecord(
            iteration=step,
            energy_hartree=round(energy, 10),
            delta_e=round(delta_e, 10),
            elapsed_ms=round(elapsed_ms, 2),
        )

    tab_match = _TABULAR_ITER_RE.match(line)
    if tab_match is not None:
        step = int(tab_match.group(1))
        energy = float(tab_match.group(2))
        delta_e = float(tab_match.group(3))
        return IterationRecord(
            iteration=step,
            energy_hartree=round(energy, 10),
            delta_e=round(delta_e, 10),
            elapsed_ms=round(elapsed_ms, 2),
        )

    return None


class _IterationStreamInterceptor(io.TextIOBase):
    def __init__(
        self,
        t0: float,
        on_iteration: Callable[[IterationRecord], None] | None,
    ) -> None:
        super().__init__()
        self._t0 = t0
        self._on_iteration = on_iteration
        self._buffer = ""
        self.records: list[IterationRecord] = []

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._handle_line(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._handle_line(self._buffer)
            self._buffer = ""

    def _handle_line(self, line: str) -> None:
        elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
        rec = parse_iteration_line(line, elapsed_ms=elapsed_ms)
        if rec is None:
            return
        self.records.append(rec)
        if self._on_iteration is not None:
            self._on_iteration(rec)


def _extract_energy(raw_result: Any) -> float:
    if isinstance(raw_result, (int, float)):
        return float(raw_result)
    for attr in ("energy_total", "total_energy", "energy", "e_total", "e_tot"):
        val = getattr(raw_result, attr, None)
        if isinstance(val, (int, float)):
            return float(val)
    if isinstance(raw_result, dict):
        for key in ("energy_total", "total_energy", "energy", "e_total", "e_tot"):
            val = raw_result.get(key)
            if isinstance(val, (int, float)):
                return float(val)
    raise TypeError(f"Unsupported mshqc return value type: {type(raw_result).__name__}")


def _resolve_reference(method_low: str, multiplicity: int) -> str:
    if method_low in ("rhf", "rmp2", "rmp3"):
        return "rhf"
    if method_low in ("uhf", "ump2", "ump3"):
        return "uhf"
    if method_low in ("rohf", "romp2", "romp3"):
        return "rohf"
    return "rhf" if multiplicity == 1 else "uhf"


def _candidate_eri_modes(basis_low: str) -> list[str]:
    env_mode = os.getenv("MSHQC_ERI_METHOD", "auto").strip().lower()
    if env_mode in ("df", "cholesky", "exact"):
        return [env_mode]
    if basis_low.startswith(("cc-p", "aug-cc-p", "def2-")):
        return ["df", "cholesky", "exact"]
    return ["cholesky", "exact", "df"]


def _build_hybrid_calculator(
    mshqc_mod: Any,
    *,
    atom_tuples: list[tuple[str, float, float, float]],
    basis: str,
    charge: int,
    multiplicity: int,
    reference: str,
) -> Any:
    basis_low = basis.strip().lower()
    basis_candidates = [_BASIS_CANONICAL.get(basis_low, basis)]
    if basis_low not in basis_candidates:
        basis_candidates.append(basis_low)

    basis_dir = os.getenv("MSHQC_BASIS_PATH", "").strip() or None
    last_exc: Exception | None = None

    for eri_mode in _candidate_eri_modes(basis_low):
        for bname in basis_candidates:
            try:
                calc = mshqc_mod.MSHQCCalculator(
                    molecule=atom_tuples,
                    basis_name=bname,
                    eri_method=eri_mode,
                    charge=charge,
                    multiplicity=multiplicity,
                    units="angstrom",
                    reference=reference,
                    print_level=0,
                    basis_dir=basis_dir,
                )
                # Eagerly verify 3-index / aux basis availability when DF is selected.
                if eri_mode == "df":
                    calc._get_ao_3index_factor(None)
                return calc
            except Exception as exc:
                last_exc = exc
                continue

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unable to initialize MSHQCCalculator.")


def _run_mshqc_hybrid_v2(
    mshqc_mod: Any,
    *,
    atoms: tuple[Atom, ...],
    method: str,
    basis: str,
    charge: int,
    multiplicity: int,
    nuclear_repulsion_hartree: float,
    on_iteration: Callable[[IterationRecord], None] | None,
) -> SolveResult:
    from mshqc.methods.omp_hybrid import run_df_omp_rhf, run_df_omp_uhf  # type: ignore
    from mshqc.methods.scf_hybrid import (  # type: ignore
        build_sad_guess,
        run_df_rhf,
        run_df_rohf,
        run_df_uhf,
    )

    m_low = method.strip().lower().replace("_", "-")
    reference = _resolve_reference(m_low, multiplicity)
    atom_tuples = [(a.symbol, float(a.x), float(a.y), float(a.z)) for a in atoms]

    t0 = time.perf_counter()
    records: list[IterationRecord] = []
    prev_energy: float | None = None

    def emit_point(energy_val: float, raw_de: float | None = None) -> None:
        nonlocal prev_energy
        step = len(records) + 1
        delta_e = (
            float(raw_de)
            if raw_de is not None and prev_energy is None
            else (0.0 if prev_energy is None else energy_val - prev_energy)
        )
        prev_energy = energy_val
        rec = IterationRecord(
            iteration=step,
            energy_hartree=round(energy_val, 10),
            delta_e=round(delta_e, 10),
            elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        )
        records.append(rec)
        if on_iteration is not None:
            on_iteration(rec)

    def scf_cb(info: dict[str, Any]) -> None:
        emit_point(float(info["energy"]))

    def omp_cb(info: dict[str, Any]) -> None:
        energy_val = info.get("energy_total")
        if energy_val is None:
            energy_val = info["energy"]
        emit_point(float(energy_val))

    calc = _build_hybrid_calculator(
        mshqc_mod,
        atom_tuples=atom_tuples,
        basis=basis,
        charge=charge,
        multiplicity=multiplicity,
        reference=reference,
    )

    H_core = calc._get_h_core()
    S_ovlp = calc._get_s_ovlp()
    v_nn = float(calc.molecule.nuclear_repulsion_energy())
    P_sad = build_sad_guess(calc.molecule, calc.basis)
    eri4_arg = calc._get_eri4() if calc.eri_method == "exact" else None
    B_ao = None if eri4_arg is not None else calc._get_ao_3index_factor(None)

    if reference == "rhf":
        scf_res = run_df_rhf(
            H_core,
            S_ovlp,
            v_nn,
            B_ao,
            calc.n_alpha,
            energy_tol=calc.threshold,
            density_tol=calc.threshold,
            P_init=P_sad,
            callback=scf_cb,
            eri4=eri4_arg,
        )
    elif reference == "rohf":
        scf_res = run_df_rohf(
            H_core,
            S_ovlp,
            v_nn,
            B_ao,
            calc.n_alpha,
            calc.n_beta,
            energy_tol=calc.threshold,
            density_tol=calc.threshold,
            P_init=P_sad,
            callback=scf_cb,
            eri4=eri4_arg,
        )
    else:
        scf_res = run_df_uhf(
            H_core,
            S_ovlp,
            v_nn,
            B_ao,
            calc.n_alpha,
            calc.n_beta,
            energy_tol=calc.threshold,
            density_tol=calc.threshold,
            P_init=P_sad,
            callback=scf_cb,
            eri4=eri4_arg,
        )

    calc.scf_res = scf_res

    if m_low in ("scf", "hf", "rhf", "uhf", "rohf"):
        final_res: Any = scf_res
    elif m_low in _OMP_STANDARD_PARAMS:
        m_name, c_os, c_ss, lam_mp3 = _OMP_STANDARD_PARAMS[m_low]
        B_corr = calc._get_ao_3index_factor(scf_res)
        if reference == "rhf":
            final_res = run_df_omp_rhf(
                H_core,
                v_nn,
                B_corr,
                scf_res.C_alpha,
                scf_res.n_occ_alpha,
                method_name=m_name,
                c_os=c_os,
                c_ss=c_ss,
                lam_mp3=lam_mp3,
                energy_tol=calc.threshold,
                callback=omp_cb,
                eri4=eri4_arg,
            )
        else:
            final_res = run_df_omp_uhf(
                H_core,
                S_ovlp,
                v_nn,
                B_corr,
                scf_res.C_alpha,
                scf_res.C_beta,
                scf_res.n_occ_alpha,
                scf_res.n_occ_beta,
                method_name=m_name,
                c_os=c_os,
                c_ss=c_ss,
                lam_mp3=lam_mp3,
                energy_tol=calc.threshold,
                callback=omp_cb,
                eri4=eri4_arg,
            )
    elif m_low in ("mp2", "rmp2", "ump2", "romp2"):
        final_res = calc.run_mp2(scf_res=scf_res)
        emit_point(_extract_energy(final_res))
    elif m_low in ("mp3", "rmp3", "ump3", "romp3"):
        final_res = calc.run_mp3(scf_res=scf_res)
        emit_point(_extract_energy(final_res))
    elif m_low in ("lt-sos-omp2", "lt-sos-oomp2"):
        final_res = calc.run_lt_sos_omp2(scf_res=scf_res)
        emit_point(_extract_energy(final_res))
    elif m_low in ("kappa-omp2", "k-omp2"):
        final_res = calc.run_kappa_omp2(scf_res=scf_res)
        emit_point(_extract_energy(final_res))
    else:
        raise ValueError(f"Unsupported mshqc hybrid-v2 method: {method}")

    energy = _extract_energy(final_res)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    if not records:
        emit_point(energy)

    version = detect_mshqc_version() or "installed"
    return SolveResult(
        engine=f"mshqc-{version}-hybrid-v2",
        method=method,
        basis=basis,
        nuclear_repulsion_hartree=round(v_nn or nuclear_repulsion_hartree, 10),
        total_energy_hartree=round(energy, 10),
        converged=bool(getattr(final_res, "converged", True)),
        iterations=[asdict(rec) for rec in records],
        wall_time_ms=round(wall_ms, 2),
    )


def run_mshqc(
    *,
    atoms: tuple[Atom, ...],
    method: str,
    basis: str,
    charge: int,
    multiplicity: int,
    nuclear_repulsion_hartree: float,
    on_iteration: Callable[[IterationRecord], None] | None,
) -> SolveResult | None:
    try:
        import mshqc  # type: ignore[import-not-found]
    except ImportError:
        return None

    if is_hybrid_v2_engine(mshqc):
        return _run_mshqc_hybrid_v2(
            mshqc,
            atoms=atoms,
            method=method,
            basis=basis,
            charge=charge,
            multiplicity=multiplicity,
            nuclear_repulsion_hartree=nuclear_repulsion_hartree,
            on_iteration=on_iteration,
        )

    geom_str = "\n".join(f"{a.symbol} {a.x:.8f} {a.y:.8f} {a.z:.8f}" for a in atoms)
    t0 = time.perf_counter()
    interceptor = _IterationStreamInterceptor(t0=t0, on_iteration=on_iteration)

    run_fn = getattr(mshqc, "run", None)
    mol_factory = getattr(mshqc, "molecule", None) or getattr(mshqc, "Molecule", None)
    energy_fn = getattr(mshqc, "energy", None)

    if run_fn is not None and "molecule" in inspect.signature(run_fn).parameters:
        atom_tuples = [(a.symbol, float(a.x), float(a.y), float(a.z)) for a in atoms]
        bname = _BASIS_CANONICAL.get(basis.strip().lower(), basis)
        with contextlib.redirect_stdout(interceptor):
            raw_out = run_fn(
                molecule=atom_tuples,
                method=method.lower(),
                basis=bname,
                charge=charge,
                multiplicity=multiplicity,
                units="angstrom",
                print_level=1,
            )
            interceptor.flush()
    elif mol_factory is not None and (energy_fn is not None or run_fn is not None):
        calc_fn = energy_fn or run_fn
        with contextlib.redirect_stdout(interceptor):
            mol = mol_factory(geom_str, charge=charge, multiplicity=multiplicity)
            raw_out = calc_fn(method.lower(), mol=mol, basis=basis)
            interceptor.flush()
    else:
        return None

    energy = _extract_energy(raw_out)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    records = interceptor.records
    if not records:
        final_rec = IterationRecord(
            iteration=1,
            energy_hartree=round(energy, 10),
            delta_e=0.0,
            elapsed_ms=round(wall_ms, 2),
        )
        if on_iteration is not None:
            on_iteration(final_rec)
        records = [final_rec]

    version = detect_mshqc_version() or "installed"
    return SolveResult(
        engine=f"mshqc-{version}",
        method=method,
        basis=basis,
        nuclear_repulsion_hartree=round(nuclear_repulsion_hartree, 10),
        total_energy_hartree=round(energy, 10),
        converged=bool(getattr(raw_out, "converged", True)),
        iterations=[asdict(rec) for rec in records],
        wall_time_ms=round(wall_ms, 2),
    )
