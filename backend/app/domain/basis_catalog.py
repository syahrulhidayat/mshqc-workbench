from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

CURATED_BASIS_SETS: tuple[str, ...] = (
    "sto-3g",
    "sto-6g",
    "3-21g",
    "6-31g",
    "6-31g_d_",
    "6-31g_d_p_",
    "6-311g",
    "6-311g_d_p_",
    "cc-pvdz",
    "cc-pvtz",
    "cc-pvqz",
    "aug-cc-pvdz",
    "aug-cc-pvtz",
    "def2-svp",
    "def2-tzvp",
    "def2-tzvpp",
    "def2-qzvp",
)

CURATED_RI_SUPPORTED: frozenset[str] = frozenset(
    {
        "cc-pvdz",
        "cc-pvtz",
        "cc-pvqz",
        "aug-cc-pvdz",
        "aug-cc-pvtz",
        "def2-svp",
        "def2-tzvp",
        "def2-tzvpp",
        "def2-qzvp",
    }
)

_AUXILIARY_SUFFIXES: tuple[str, ...] = (
    "-ri",
    "-jkfit",
    "-jfit",
    "-optri",
    "-dual",
    "-autoabs",
    "-autoaux",
    "-deprecated",
    "-minao",
    "-canonical",
)

_EXCLUDED_EXACT: frozenset[str] = frozenset({"sad-fit", "pe_ecp"})
_EXCLUDED_PREFIXES: tuple[str, ...] = ("sap_",)
_SAFE_BASIS_RE = re.compile(r"^[a-z0-9][a-z0-9_.+-]{1,63}$")


@dataclass(frozen=True)
class BasisCatalog:
    basis_sets: tuple[str, ...]
    ri_supported: tuple[str, ...]
    basis_dir: str | None
    discovered_count: int


def _candidate_basis_dirs(explicit_dir: str | Path | None = None) -> list[Path]:
    dirs: list[Path] = []
    if explicit_dir:
        dirs.append(Path(explicit_dir))
    for env_key in ("MSHQC_BASIS_PATH", "MSHQC_BASIS_DIR"):
        raw = os.getenv(env_key, "").strip()
        if raw:
            dirs.append(Path(raw))
    dirs.extend(
        [
            Path("data/basis"),
            Path("../mshqc-basis/data/basis"),
            Path("../../mshqc-basis/data/basis"),
            Path.home() / "mshqc-basis" / "data" / "basis",
            Path("/data/basis"),
        ]
    )
    return dirs


def _is_orbital_basis(stem: str) -> bool:
    if stem in _EXCLUDED_EXACT:
        return False
    if any(stem.startswith(pref) for pref in _EXCLUDED_PREFIXES):
        return False
    if any(stem.endswith(suf) for suf in _AUXILIARY_SUFFIXES):
        return False
    return bool(_SAFE_BASIS_RE.match(stem))


def discover_basis_catalog(explicit_dir: str | Path | None = None) -> BasisCatalog:
    for cand in _candidate_basis_dirs(explicit_dir):
        if not cand.is_dir():
            continue
        gbs_files = sorted(cand.glob("*.gbs"))
        if not gbs_files:
            continue

        all_stems = {f.stem.lower() for f in gbs_files}
        orbital = [stem for stem in sorted(all_stems) if _is_orbital_basis(stem)]
        if not orbital:
            continue

        curated_rank = {name: idx for idx, name in enumerate(CURATED_BASIS_SETS)}
        ordered = sorted(
            orbital,
            key=lambda name: (0, curated_rank[name]) if name in curated_rank else (1, name),
        )
        ri_ready = tuple(name for name in ordered if f"{name}-ri" in all_stems)
        return BasisCatalog(
            basis_sets=tuple(ordered),
            ri_supported=ri_ready,
            basis_dir=str(cand.resolve()),
            discovered_count=len(ordered),
        )

    return BasisCatalog(
        basis_sets=CURATED_BASIS_SETS,
        ri_supported=tuple(b for b in CURATED_BASIS_SETS if b in CURATED_RI_SUPPORTED),
        basis_dir=None,
        discovered_count=0,
    )


def normalize_and_validate_basis(raw_basis: str, catalog: BasisCatalog | None = None) -> str:
    cleaned = raw_basis.strip().lower()
    if cleaned.endswith(".gbs"):
        cleaned = cleaned[:-4]
    if not _SAFE_BASIS_RE.match(cleaned):
        raise ValueError(f"Invalid basis set identifier '{raw_basis}'.")

    cat = catalog or discover_basis_catalog()
    if cleaned not in cat.basis_sets:
        raise ValueError(
            f"Unsupported basis set '{raw_basis}'. "
            "Check /api/v1/capabilities for available basis sets."
        )
    return cleaned
