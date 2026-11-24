from __future__ import annotations

import contextlib
import importlib.metadata
import io
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
    for attr in ("total_energy", "energy", "e_total", "e_tot"):
        val = getattr(raw_result, attr, None)
        if isinstance(val, (int, float)):
            return float(val)
    if isinstance(raw_result, dict):
        for key in ("total_energy", "energy", "e_total", "e_tot"):
            val = raw_result.get(key)
            if isinstance(val, (int, float)):
                return float(val)
    raise TypeError(f"Unsupported mshqc return value type: {type(raw_result).__name__}")


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

    mol_factory = getattr(mshqc, "molecule", None) or getattr(mshqc, "Molecule", None)
    calc_fn = getattr(mshqc, "energy", None) or getattr(mshqc, "run", None)
    if mol_factory is None or calc_fn is None:
        return None

    geom_str = "\n".join(f"{a.symbol} {a.x:.8f} {a.y:.8f} {a.z:.8f}" for a in atoms)
    t0 = time.perf_counter()
    interceptor = _IterationStreamInterceptor(t0=t0, on_iteration=on_iteration)

    with contextlib.redirect_stdout(interceptor):
        mol = mol_factory(geom_str, charge=charge, multiplicity=multiplicity)
        raw_out = calc_fn(method.lower(), mol=mol, basis=basis)
    interceptor.flush()

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
        converged=True,
        iterations=[asdict(rec) for rec in records],
        wall_time_ms=round(wall_ms, 2),
    )
