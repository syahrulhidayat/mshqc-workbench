import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Capabilities,
  EriOption,
  IterationPoint,
  JobRecord,
  MethodOption,
  cancelJob,
  fetchCapabilities,
  fetchJobs,
  retryJob,
  submitJob,
  subscribeJobEvents
} from "./api";
import { ConvergenceChart } from "./components/ConvergenceChart";
import { MoleculeViewer3D } from "./components/MoleculeViewer3D";

interface MoleculePreset {
  label: string;
  xyz: string;
  charge: number;
  multiplicity: number;
  recommendedMethod: MethodOption;
}

const PRESETS: Record<string, MoleculePreset> = {
  H2O: {
    label: "H2O",
    xyz: "O  0.000000  0.000000  0.117300\nH  0.000000  0.757200 -0.469200\nH  0.000000 -0.757200 -0.469200",
    charge: 0,
    multiplicity: 1,
    recommendedMethod: "OMP2"
  },
  NH3: {
    label: "NH3",
    xyz: "N  0.000000  0.000000  0.116500\nH  0.000000  0.939700 -0.271800\nH  0.813800 -0.469850 -0.271800\nH -0.813800 -0.469850 -0.271800",
    charge: 0,
    multiplicity: 1,
    recommendedMethod: "SCS-OMP2"
  },
  CH4: {
    label: "CH4",
    xyz: "C  0.000000  0.000000  0.000000\nH  0.629118  0.629118  0.629118\nH -0.629118 -0.629118  0.629118\nH -0.629118  0.629118 -0.629118\nH  0.629118 -0.629118 -0.629118",
    charge: 0,
    multiplicity: 1,
    recommendedMethod: "LT-SOS-OMP2"
  },
  CO: {
    label: "CO",
    xyz: "C  0.000000  0.000000 -0.564200\nO  0.000000  0.000000  0.564200",
    charge: 0,
    multiplicity: 1,
    recommendedMethod: "OMP3"
  },
  O2: {
    label: "O2 (Triplet)",
    xyz: "O  0.000000  0.000000 -0.603500\nO  0.000000  0.000000  0.603500",
    charge: 0,
    multiplicity: 3,
    recommendedMethod: "UHF"
  },
  OH: {
    label: "OH Radical",
    xyz: "O  0.000000  0.000000  0.000000\nH  0.000000  0.000000  0.969700",
    charge: 0,
    multiplicity: 2,
    recommendedMethod: "OMP2"
  },
  C6H6: {
    label: "Benzene",
    xyz:
      "C  1.3965  0.0000  0.0000\nC  0.6982  1.2094  0.0000\nC -0.6982  1.2094  0.0000\n" +
      "C -1.3965  0.0000  0.0000\nC -0.6982 -1.2094  0.0000\nC  0.6982 -1.2094  0.0000\n" +
      "H  2.4825  0.0000  0.0000\nH  1.2412  2.1499  0.0000\nH -1.2412  2.1499  0.0000\n" +
      "H -2.4825  0.0000  0.0000\nH -1.2412 -2.1499  0.0000\nH  1.2412 -2.1499  0.0000",
    charge: 0,
    multiplicity: 1,
    recommendedMethod: "LT-SOS-OMP2"
  }
};

const METHOD_GROUPS: { label: string; items: MethodOption[] }[] = [
  { label: "SCF Reference", items: ["RHF", "UHF", "ROHF"] },
  { label: "Canonical Perturbation", items: ["RMP2", "UMP2", "ROMP2", "RMP3", "UMP3", "ROMP3"] },
  {
    label: "Orbital-Optimized (hybrid-v2)",
    items: ["OMP2", "SCS-OMP2", "SOS-OMP2", "LT-SOS-OMP2", "OMP2.5", "OMP3", "KAPPA-OMP2"]
  }
];

const FALLBACK_BASIS = ["sto-3g", "6-31g", "cc-pvdz", "cc-pvtz", "def2-svp", "def2-tzvp"];

function formatEnergy(val: number | null | undefined, signed = false): string {
  if (val === null || val === undefined || !Number.isFinite(val)) return "—";
  return `${signed && val > 0 ? "+" : ""}${val.toFixed(8)} Ha`;
}

export function App() {
  const [geometryXyz, setGeometryXyz] = useState(PRESETS.H2O.xyz);
  const [method, setMethod] = useState<MethodOption>("OMP2");
  const [basis, setBasis] = useState<string>("cc-pvdz");
  const [basisFilter, setBasisFilter] = useState("");
  const [charge, setCharge] = useState(0);
  const [multiplicity, setMultiplicity] = useState(1);
  const [eriMethod, setEriMethod] = useState<EriOption>("auto");
  const [freezeCore, setFreezeCore] = useState(false);

  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [activeJob, setActiveJob] = useState<JobRecord | null>(null);
  const [points, setPoints] = useState<IterationPoint[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const unsubscribeRef = useRef<(() => void) | null>(null);

  const basisSets = capabilities?.basis_sets.length ? capabilities.basis_sets : FALLBACK_BASIS;
  const riSet = useMemo(() => new Set(capabilities?.ri_basis_sets ?? []), [capabilities]);

  const filteredBasis = useMemo(() => {
    const q = basisFilter.trim().toLowerCase();
    if (!q) return basisSets;
    const matches = basisSets.filter((b) => b.toLowerCase().includes(q));
    return matches.includes(basis) ? matches : [basis, ...matches];
  }, [basisSets, basisFilter, basis]);

  const refreshHistory = async () => {
    try {
      const recent = await fetchJobs();
      setJobs(recent);
    } catch {
      // Keep existing list on transient poll failure.
    }
  };

  useEffect(() => {
    void fetchCapabilities()
      .then((caps) => setCapabilities(caps))
      .catch(() => setCapabilities(null));
    void refreshHistory();
    return () => {
      unsubscribeRef.current?.();
    };
  }, []);

  const attachEventStream = (jobId: string) => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = subscribeJobEvents(jobId, {
      onIteration: (pt) => {
        setPoints((prev) => [...prev, pt]);
      },
      onDone: (done) => {
        setActiveJob((prev) =>
          prev
            ? {
                ...prev,
                status: done.status,
                engine: done.engine,
                result: done.result,
                error_message: done.error_message
              }
            : prev
        );
        if (done.result?.iterations) {
          setPoints(done.result.iterations);
        }
        void refreshHistory();
      },
      onError: () => {
        void refreshHistory();
      }
    });
  };

  const handleSubmit = async (evt: FormEvent) => {
    evt.preventDefault();
    setError(null);
    setSubmitting(true);
    unsubscribeRef.current?.();

    try {
      const job = await submitJob({
        geometry_xyz: geometryXyz,
        method,
        basis,
        charge,
        multiplicity,
        eri_method: eriMethod,
        freeze_core: freezeCore
      });
      setActiveJob(job);
      setPoints(job.result?.iterations ?? []);
      void refreshHistory();

      if (job.status === "queued" || job.status === "running") {
        attachEventStream(job.id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit job.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async (jobId: string) => {
    setActionBusy(true);
    setError(null);
    try {
      const updated = await cancelJob(jobId);
      unsubscribeRef.current?.();
      setActiveJob(updated);
      void refreshHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cancel request failed.");
    } finally {
      setActionBusy(false);
    }
  };

  const handleRetry = async (jobId: string) => {
    setActionBusy(true);
    setError(null);
    try {
      const requeued = await retryJob(jobId);
      setActiveJob(requeued);
      setPoints([]);
      attachEventStream(requeued.id);
      void refreshHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Retry request failed.");
    } finally {
      setActionBusy(false);
    }
  };

  const inspectJob = (job: JobRecord) => {
    unsubscribeRef.current?.();
    setActiveJob(job);
    setGeometryXyz(job.geometry_xyz);
    setMethod(job.method);
    setBasis(job.basis);
    setCharge(job.charge);
    setMultiplicity(job.multiplicity);
    setEriMethod(job.eri_method || "auto");
    setFreezeCore(Boolean(job.freeze_core));
    setPoints(job.result?.iterations ?? []);
    if (job.status === "queued" || job.status === "running") {
      attachEventStream(job.id);
    }
  };

  const isNativeEngine = Boolean(capabilities?.active_engine.startsWith("mshqc"));
  const canCancel = activeJob?.status === "queued" || activeJob?.status === "running";
  const canRetry = activeJob?.status === "failed" || activeJob?.status === "cancelled";

  return (
    <div className="layout">
      <header className="topbar">
        <div>
          <h1>mshqc-workbench</h1>
          <p className="subtitle">
            Asynchronous electronic-structure workbench · SHA-256 deduplication · live SSE convergence
          </p>
        </div>
        <div className="topbar-meta">
          <span className={`pill ${isNativeEngine ? "pill-native" : "pill-sim"}`}>
            {capabilities ? capabilities.active_engine : "connecting..."}
          </span>
          <span className="pill">
            {capabilities && capabilities.discovered_basis_count > 0
              ? `${capabilities.discovered_basis_count} basis (.gbs)`
              : `${basisSets.length} curated basis`}
          </span>
        </div>
      </header>

      {capabilities && !isNativeEngine && (
        <div className="engine-banner banner-warn" role="status">
          <strong>Surrogate Simulation Mode (Non-Physical Estimate):</strong> Running deterministic
          fallback because <code>mshqc</code> is not installed in this container. To run authentic
          C++20 <code>hybrid-v2</code> ab initio kernels, boot <code>make dev-infra</code> and start
          API/Worker inside your <code>mshqc_314</code> environment with{" "}
          <code>SOLVER_BACKEND=mshqc</code>.
        </div>
      )}

      {capabilities && isNativeEngine && (
        <div className="engine-banner banner-ok" role="status">
          <strong>Native Ab Initio Engine Active:</strong> Executing C++20{" "}
          <code>{capabilities.active_engine}</code> kernels with{" "}
          {capabilities.ri_basis_sets.length} DF-RI auxiliary basis pairs detected.
        </div>
      )}

      <main className="grid">
        <section className="panel">
          <h2>Molecular Input & Method</h2>
          <div className="preset-row">
            {Object.entries(PRESETS).map(([key, preset]) => (
              <button
                key={key}
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setGeometryXyz(preset.xyz);
                  setCharge(preset.charge);
                  setMultiplicity(preset.multiplicity);
                  setMethod(preset.recommendedMethod);
                }}
              >
                {preset.label}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="form">
            <label>
              <span>Geometry (XYZ in Angstrom)</span>
              <textarea
                rows={6}
                value={geometryXyz}
                onChange={(e) => setGeometryXyz(e.target.value)}
                spellCheck={false}
                required
              />
            </label>

            <div className="field-row">
              <label>
                <span>Electronic Method</span>
                <select value={method} onChange={(e) => setMethod(e.target.value as MethodOption)}>
                  {METHOD_GROUPS.map((group) => (
                    <optgroup key={group.label} label={group.label}>
                      {group.items.map((item) => (
                        <option key={item} value={item}>
                          {item}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </label>

              <label>
                <span>ERI Integral Mode</span>
                <select
                  value={eriMethod}
                  onChange={(e) => setEriMethod(e.target.value as EriOption)}
                >
                  <option value="auto">auto (DF if RI available)</option>
                  <option value="df">df (Density Fitting / RI)</option>
                  <option value="cholesky">cholesky (Cholesky ERI)</option>
                  <option value="exact">exact (4-Index Exact ERI)</option>
                </select>
              </label>
            </div>

            <div className="basis-block">
              <div className="basis-head">
                <span className="field-label-inline">Basis Set ({filteredBasis.length})</span>
                {riSet.has(basis) ? (
                  <span className="badge badge-cache">DF-RI Ready</span>
                ) : (
                  <span className="badge">Cholesky / Exact</span>
                )}
              </div>
              {basisSets.length > 15 && (
                <input
                  type="search"
                  placeholder="Filter basis (e.g. cc-pv, def2, aug-, 6-311)..."
                  value={basisFilter}
                  onChange={(e) => setBasisFilter(e.target.value)}
                />
              )}
              <select value={basis} onChange={(e) => setBasis(e.target.value)}>
                {filteredBasis.map((b) => (
                  <option key={b} value={b}>
                    {riSet.has(b) ? `${b}  [DF-RI]` : b}
                  </option>
                ))}
              </select>
            </div>

            <div className="field-row">
              <label>
                <span>Charge</span>
                <input
                  type="number"
                  min={-4}
                  max={4}
                  value={charge}
                  onChange={(e) => setCharge(Number(e.target.value))}
                />
              </label>

              <label>
                <span>Multiplicity (2S+1)</span>
                <input
                  type="number"
                  min={1}
                  max={7}
                  value={multiplicity}
                  onChange={(e) => setMultiplicity(Number(e.target.value))}
                />
              </label>
            </div>

            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={freezeCore}
                onChange={(e) => setFreezeCore(e.target.checked)}
              />
              <span>Freeze Core Orbitals (exclude inner 1s/core shells from correlation)</span>
            </label>

            {error && <div className="alert-error">{error}</div>}

            <button type="submit" className="btn-primary" disabled={submitting}>
              {submitting ? "Submitting..." : `Run ${method} / ${basis}`}
            </button>
          </form>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Live Telemetry & Quantum Results</h2>
            {activeJob && (
              <div className="badges">
                <span className={`badge status-${activeJob.status}`}>
                  {activeJob.status.toUpperCase()}
                </span>
                {activeJob.cache_hit && <span className="badge badge-cache">CACHE HIT</span>}
                {activeJob.engine && <span className="badge badge-engine">{activeJob.engine}</span>}
                {canCancel && (
                  <button
                    type="button"
                    className="btn-danger"
                    disabled={actionBusy}
                    onClick={() => void handleCancel(activeJob.id)}
                  >
                    Cancel
                  </button>
                )}
                {canRetry && (
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={actionBusy}
                    onClick={() => void handleRetry(activeJob.id)}
                  >
                    Retry
                  </button>
                )}
              </div>
            )}
          </div>

          <MoleculeViewer3D geometryXyz={geometryXyz} />

          {activeJob?.result && (
            <div className="metrics-grid">
              <div className="metric-card">
                <span className="metric-label">Total Energy</span>
                <strong className="mono">{formatEnergy(activeJob.result.total_energy_hartree)}</strong>
              </div>
              <div className="metric-card">
                <span className="metric-label">SCF Reference</span>
                <strong className="mono">{formatEnergy(activeJob.result.energy_scf_hartree)}</strong>
              </div>
              <div className="metric-card">
                <span className="metric-label">Correlation (E_corr)</span>
                <strong className="mono">
                  {formatEnergy(activeJob.result.energy_corr_hartree, true)}
                </strong>
              </div>
              <div className="metric-card">
                <span className="metric-label">Nuclear Repulsion</span>
                <strong className="mono">
                  {formatEnergy(activeJob.result.nuclear_repulsion_hartree)}
                </strong>
              </div>
              <div className="metric-card">
                <span className="metric-label">Spin & Basis</span>
                <strong className="mono">
                  {`<S²>=${activeJob.result.s2 !== null && activeJob.result.s2 !== undefined ? activeJob.result.s2.toFixed(4) : "—"} · Nbf=${activeJob.result.n_basis_functions ?? "—"}`}
                </strong>
              </div>
              <div className="metric-card">
                <span className="metric-label">Runtime & ERI</span>
                <strong className="mono">
                  {`${activeJob.result.wall_time_ms.toFixed(1)} ms · ${(activeJob.result.eri_method || activeJob.eri_method).toUpperCase()}${activeJob.result.n_frozen ? ` · FC(${activeJob.result.n_frozen})` : ""}`}
                </strong>
              </div>
            </div>
          )}

          {activeJob?.error_message && (
            <div className="alert-error" style={{ marginBottom: 12 }}>
              {activeJob.error_message}
            </div>
          )}

          <ConvergenceChart points={points} />

          <div className="terminal" aria-live="polite">
            {points.length === 0 ? (
              <div className="muted">Waiting for solver iterations via SSE...</div>
            ) : (
              points.map((pt, idx) => (
                <div key={`${pt.iteration}-${idx}`} className="log-line">
                  {`[${(pt.stage || "scf").toUpperCase().padEnd(3)}] Iter ${String(pt.iteration).padStart(2, "0")} | E = ${pt.energy_hartree.toFixed(10)} Ha | dE = ${pt.delta_e.toExponential(3)} | t = ${pt.elapsed_ms.toFixed(1)} ms`}
                </div>
              ))
            )}
          </div>
        </section>
      </main>

      <section className="panel">
        <div className="panel-head">
          <h2>Recent Jobs (Persisted in PostgreSQL)</h2>
          <button type="button" className="btn-secondary" onClick={() => void refreshHistory()}>
            Refresh
          </button>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>SHA-256</th>
                <th>Method / Basis</th>
                <th>Options</th>
                <th>Engine</th>
                <th>Status</th>
                <th>Total Energy (Ha)</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {jobs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="muted">
                    No calculations submitted yet.
                  </td>
                </tr>
              ) : (
                jobs.map((job) => (
                  <tr
                    key={job.id}
                    onClick={() => inspectJob(job)}
                    className={activeJob?.id === job.id ? "row-active" : ""}
                  >
                    <td className="mono">{job.input_hash.slice(0, 12)}</td>
                    <td className="mono">
                      {job.method} / {job.basis}
                    </td>
                    <td className="mono">
                      {(job.eri_method || "auto").toUpperCase()}
                      {job.freeze_core ? " · FC" : ""}
                      {` · q=${job.charge}, m=${job.multiplicity}`}
                    </td>
                    <td className="mono">{job.engine ?? "—"}</td>
                    <td>
                      <span className={`badge status-${job.status}`}>{job.status}</span>
                    </td>
                    <td className="mono">
                      {job.result ? job.result.total_energy_hartree.toFixed(8) : "—"}
                    </td>
                    <td>
                      {(job.status === "queued" || job.status === "running") && (
                        <button
                          type="button"
                          className="btn-danger"
                          disabled={actionBusy}
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleCancel(job.id);
                          }}
                        >
                          Cancel
                        </button>
                      )}
                      {(job.status === "failed" || job.status === "cancelled") && (
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={actionBusy}
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleRetry(job.id);
                          }}
                        >
                          Retry
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export default App;
