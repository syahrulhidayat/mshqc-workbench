from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.geometry import parse_xyz
from app.domain.mshqc_adapter import IterationRecord, parse_iteration_line, run_mshqc


def test_parse_iteration_line_extracts_scf_and_omp_formats() -> None:
    kv = parse_iteration_line("Iter 3: E = -76.02674512 dE = -1.25e-05", elapsed_ms=12.4)
    assert kv is not None
    assert kv.iteration == 3
    assert kv.energy_hartree == pytest.approx(-76.02674512)
    assert kv.delta_e == pytest.approx(-1.25e-5)

    tab = parse_iteration_line("RHF   4   -76.0268001122   -9.11e-06", elapsed_ms=18.0)
    assert tab is not None
    assert tab.iteration == 4
    assert tab.energy_hartree == pytest.approx(-76.0268001122)

    assert parse_iteration_line("Iter   Energy (Hartree)   dE", elapsed_ms=1.0) is None


def test_run_mshqc_streams_stdout_iterations_with_stub_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mod = types.ModuleType("mshqc")
    fake_mod.__version__ = "1.0.5"  # type: ignore[attr-defined]

    def fake_molecule(geom: str, *, charge: int = 0, multiplicity: int = 1) -> dict[str, object]:
        return {"geom": geom, "charge": charge, "multiplicity": multiplicity}

    def fake_energy(method: str, *, mol: dict[str, object], basis: str) -> float:
        assert method == "omp2"
        assert basis == "cc-pvdz"
        assert mol["charge"] == 0
        print("Iter 1: E = -76.01000000 dE = -1.00e-01")
        print("Iter 2: E = -76.24123456 dE = -2.31e-01")
        return -76.24123456

    fake_mod.molecule = fake_molecule  # type: ignore[attr-defined]
    fake_mod.energy = fake_energy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mshqc", fake_mod)

    streamed: list[IterationRecord] = []
    atoms = parse_xyz("O 0 0 0\nH 0.757 0.586 0\nH -0.757 0.586 0")
    res = run_mshqc(
        atoms=atoms,
        method="OMP2",
        basis="cc-pvdz",
        charge=0,
        multiplicity=1,
        nuclear_repulsion_hartree=9.19,
        on_iteration=streamed.append,
    )

    assert res is not None
    assert res.engine == "mshqc-1.0.5"
    assert res.total_energy_hartree == pytest.approx(-76.24123456)
    assert len(streamed) == 2
    assert streamed[-1].iteration == 2


def test_capabilities_endpoint_returns_supported_methods_and_engine() -> None:
    app = create_app()
    app.state.repo = object()
    app.state.redis = object()

    with TestClient(app) as client:
        resp = client.get("/api/v1/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert "active_engine" in body
        assert "OMP2" in body["methods"]
        assert "OMP3" in body["methods"]
        assert "cc-pvtz" in body["basis_sets"]
