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


def test_run_mshqc_hybrid_v2_streams_structured_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import dataclass

    @dataclass
    class StubSCF:
        energy_total: float = -76.02100000
        converged: bool = True
        C_alpha: object = None
        C_beta: object = None
        n_occ_alpha: int = 5
        n_occ_beta: int = 5

    @dataclass
    class StubOMP:
        energy_total: float = -76.24567890
        converged: bool = True

    class StubMol:
        def nuclear_repulsion_energy(self) -> float:
            return 9.19012345

    captured_kwargs: dict[str, object] = {}

    class StubCalculator:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)
            self.molecule = StubMol()
            self.basis = object()
            self.eri_method = str(kwargs.get("eri_method", "df"))
            self.threshold = 1e-9
            self.n_alpha = 5
            self.n_beta = 5
            self.scf_res: object = None

        def _get_h_core(self) -> object:
            return "H"

        def _get_s_ovlp(self) -> object:
            return "S"

        def _get_eri4(self) -> object:
            return None

        def _get_ao_3index_factor(self, _scf: object = None) -> object:
            return "B_ao"

    def fake_build_sad(_mol: object, _basis: object) -> object:
        return "P_sad"

    def fake_run_df_rhf(*_args: object, **kwargs: object) -> StubSCF:
        cb = kwargs.get("callback")
        if callable(cb):
            cb({"iter": 1, "energy": -75.90000000})
            cb({"iter": 2, "energy": -76.02100000})
        return StubSCF()

    def fake_run_df_omp_rhf(*_args: object, **kwargs: object) -> StubOMP:
        cb = kwargs.get("callback")
        if callable(cb):
            cb({"iter": 0, "energy_total": -76.23000000})
            cb({"iter": 1, "energy_total": -76.24567890})
        return StubOMP()

    fake_pkg = types.ModuleType("mshqc")
    fake_pkg.__version__ = "1.0.4.post22"  # type: ignore[attr-defined]
    fake_pkg.MSHQCCalculator = StubCalculator  # type: ignore[attr-defined]

    fake_methods = types.ModuleType("mshqc.methods")
    fake_scf_mod = types.ModuleType("mshqc.methods.scf_hybrid")
    fake_scf_mod.build_sad_guess = fake_build_sad  # type: ignore[attr-defined]
    fake_scf_mod.run_df_rhf = fake_run_df_rhf  # type: ignore[attr-defined]
    fake_scf_mod.run_df_uhf = fake_run_df_rhf  # type: ignore[attr-defined]
    fake_scf_mod.run_df_rohf = fake_run_df_rhf  # type: ignore[attr-defined]

    fake_omp_mod = types.ModuleType("mshqc.methods.omp_hybrid")
    fake_omp_mod.run_df_omp_rhf = fake_run_df_omp_rhf  # type: ignore[attr-defined]
    fake_omp_mod.run_df_omp_uhf = fake_run_df_omp_rhf  # type: ignore[attr-defined]

    fake_pkg.methods = fake_methods  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mshqc", fake_pkg)
    monkeypatch.setitem(sys.modules, "mshqc.methods", fake_methods)
    monkeypatch.setitem(sys.modules, "mshqc.methods.scf_hybrid", fake_scf_mod)
    monkeypatch.setitem(sys.modules, "mshqc.methods.omp_hybrid", fake_omp_mod)

    streamed: list[IterationRecord] = []
    atoms = parse_xyz("O 0 0 0\nH 0.757 0.586 0\nH -0.757 0.586 0")
    res = run_mshqc(
        atoms=atoms,
        method="SCS-OMP2",
        basis="cc-pvdz",
        charge=0,
        multiplicity=1,
        nuclear_repulsion_hartree=9.19,
        on_iteration=streamed.append,
    )

    assert res is not None
    assert res.engine == "mshqc-1.0.4.post22-hybrid-v2"
    assert captured_kwargs["units"] == "angstrom"
    assert captured_kwargs["basis_name"] == "cc-pVDZ"
    assert res.total_energy_hartree == pytest.approx(-76.24567890)
    assert len(streamed) == 4
    assert [pt.iteration for pt in streamed] == [1, 2, 3, 4]
