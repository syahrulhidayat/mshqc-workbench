from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.basis_catalog import discover_basis_catalog, normalize_and_validate_basis
from app.domain.geometry import parse_xyz
from app.domain.hashing import canonical_job_hash


def test_basis_catalog_filters_auxiliary_and_detects_ri(tmp_path: Path) -> None:
    files = [
        "cc-pvdz.gbs",
        "cc-pvdz-ri.gbs",
        "cc-pvdz-jkfit.gbs",
        "def2-tzvp.gbs",
        "def2-tzvp-ri.gbs",
        "sto-3g.gbs",
        "6-31g_d_p_.gbs",
        "cc-pcvdz-deprecated.gbs",
        "sad-fit.gbs",
        "sap_grasp_small.gbs",
        "pe_ecp.gbs",
    ]
    for name in files:
        (tmp_path / name).write_text("spherical\n****\n", encoding="utf-8")

    catalog = discover_basis_catalog(tmp_path)
    assert catalog.discovered_count == 4
    assert set(catalog.basis_sets) == {"sto-3g", "6-31g_d_p_", "cc-pvdz", "def2-tzvp"}
    assert set(catalog.ri_supported) == {"cc-pvdz", "def2-tzvp"}

    assert normalize_and_validate_basis("CC-PVDZ", catalog) == "cc-pvdz"
    with pytest.raises(ValueError, match="Unsupported basis set"):
        normalize_and_validate_basis("cc-pvdz-ri", catalog)


def test_canonical_hash_isolates_engine_family_and_options() -> None:
    atoms = parse_xyz("Fe 0 0 0\nO 0 0 1.62")
    h_surr = canonical_job_hash(
        atoms=atoms,
        method="OMP2",
        basis="cc-pvdz",
        charge=0,
        multiplicity=5,
        engine_family="surrogate",
    )
    h_mshqc = canonical_job_hash(
        atoms=atoms,
        method="OMP2",
        basis="cc-pvdz",
        charge=0,
        multiplicity=5,
        engine_family="mshqc",
    )
    h_fc = canonical_job_hash(
        atoms=atoms,
        method="OMP2",
        basis="cc-pvdz",
        charge=0,
        multiplicity=5,
        engine_family="mshqc",
        freeze_core=True,
    )
    assert h_surr != h_mshqc
    assert h_mshqc != h_fc
