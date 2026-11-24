from __future__ import annotations

import pytest

from app.domain.geometry import parse_xyz, validate_spin_parity
from app.domain.hashing import canonical_job_hash
from app.domain.solver import execute_solver


def test_parse_xyz_supports_plain_and_header_formats() -> None:
    plain = "O 0.0 0.0 0.0\nH 0.757 0.586 0.0\nH -0.757 0.586 0.0"
    header = "3\nWater molecule\n" + plain
    assert parse_xyz(plain) == parse_xyz(header)


def test_parse_xyz_rejects_overlapping_atoms() -> None:
    with pytest.raises(ValueError, match="overlap"):
        parse_xyz("H 0.0 0.0 0.0\nH 0.0 0.0 0.00001")


def test_spin_parity_rejects_impossible_multiplicity() -> None:
    atoms = parse_xyz("O 0 0 0\nH 0.757 0.586 0\nH -0.757 0.586 0")
    with pytest.raises(ValueError, match="Spin parity mismatch"):
        validate_spin_parity(atoms, charge=0, multiplicity=2)


def test_canonical_hash_is_invariant_to_whitespace_and_trailing_zeros() -> None:
    a1 = parse_xyz("O 0 0 0\nH 0.757 0.586 0\nH -0.757 0.586 0")
    a2 = parse_xyz("o   0.0000   0.0  0.00\nh 0.757000 0.5860 0.0\nh -0.757 0.586 0.000")
    h1 = canonical_job_hash(atoms=a1, method="RHF", basis="cc-pvdz", charge=0, multiplicity=1)
    h2 = canonical_job_hash(atoms=a2, method="rhf", basis="CC-PVDZ", charge=0, multiplicity=1)
    assert h1 == h2


def test_surrogate_solver_converges_deterministically() -> None:
    atoms = parse_xyz("O 0 0 0\nH 0.757 0.586 0\nH -0.757 0.586 0")
    events: list[int] = []
    res = execute_solver(
        atoms=atoms,
        method="RHF",
        basis="cc-pvdz",
        charge=0,
        multiplicity=1,
        backend_preference="surrogate",
        on_iteration=lambda r: events.append(r.iteration),
        step_delay_s=0.0,
    )
    assert res.converged is True
    assert len(events) == len(res.iterations) >= 5
    assert res.nuclear_repulsion_hartree > 0
