import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  type BasisOption,
  cancelJob,
  type Capabilities,
  fetchCapabilities,
  fetchJobs,
  type IterationPoint,
  type JobRecord,
  type MethodOption,
  retryJob,
  submitJob,
  subscribeJobEvents
} from "./api";
import { ConvergenceChart } from "./components/ConvergenceChart";
import { MoleculeViewer3D } from "./components/MoleculeViewer3D";

const DEFAULT_METHODS: MethodOption[] = ["RHF", "UHF", "ROHF", "RMP2", "UMP2", "ROMP2", "RMP3", "UMP3", "OMP2", "SCS-OMP2", "SOS-OMP2", "LT-SOS-OMP2", "OMP2.5", "OMP3", "KAPPA-OMP2"];
const DEFAULT_BASIS_SETS: BasisOption[] = ["sto-3g", "6-31g", "cc-pvdz", "cc-pvtz"];

const PRESETS: Record<string, string> = {
  "H2O (Water)":
    "O  0.000000  0.000000  0.000000\nH  0.757000  0.586000  0.000000\nH -0.757000  0.586000  0.000000",
  "CH4 (Methane)":
    "C  0.000000  0.000000  0.000000\nH  0.629118  0.629118  0.629118\nH -0.629118 -0.629118  0.629118\nH -0.629118  0.629118 -0.629118\nH  0.629118 -0.629118 -0.629118",
  "NH3 (Ammonia)":
    "N  0.000000  0.000000  0.116489\nH  0.000000  0.939731 -0.271808\nH  0.813831 -0.469865 -0.271808\nH -0.813831 -0.469865 -0.271808"
};

export default function App() {
  const [geometry, setGeometry] = useState(PRESETS["H2O (Water)"]);
  const [method, setMethod] = useState<MethodOption>("RHF");
  const [basis, setBasis] = useState<BasisOption>("cc-pvdz");
  const [charge, setCharge] = useState(0);
  const [multiplicity, setMultiplicity] = useState(1);

  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [activeJob, setActiveJob] = useState<JobRecord | null>(null);
  const [iterations, setIterations] = useState<IterationPoint[]>([]);
  const [history, setHistory] = useState<JobRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const unsubscribeRef = useRef<(() => void) | null>(null);

  const refreshHistory = async () => {
    try {
      const items = await fetchJobs();
      setHistory(items);
    } catch {
      // Initial load may race with backend startup; submission surfaces explicit errors.
    }
  };

  useEffect(() => {
    void refreshHistory();
    void fetchCapabilities()
      .then((caps) => setCapabilities(caps))
      .catch(() => undefined);
    return () => {
      if (unsubscribeRef.current) unsubscribeRef.current();
    };
  }, []);

  const attachStream = (job: JobRecord) => {
    if (unsubscribeRef.current) unsubscribeRef.current();
    setActiveJob(job);
    setGeometry(job.geometry_xyz);
    setIterations(job.result?.iterations ?? []);

    if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
      return;
    }

    unsubscribeRef.current = subscribeJobEvents(job.id, {
      onIteration: (point) => {
        setIterations((prev) => {
          if (prev.some((p) => p.iteration === point.iteration)) return prev;
          return [...prev, point];
        });
      },
      onDone: (payload) => {
        setActiveJob((prev) =>
          prev
            ? {
                ...prev,
                status: payload.status,
                engine: payload.engine,
                result: payload.result,
                error_message: payload.error_message
              }
            : null
        );
        if (payload.result?.iterations) {
          setIterations(payload.result.iterations);
        }
        void refreshHistory();
      },
      onError: () => {
        void refreshHistory();
      }
    });
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const job = await submitJob({
        geometry_xyz: geometry,
        method,
        basis,
        charge,
        multiplicity
      });
      attachStream(job);
      void refreshHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit job.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async () => {
    if (!activeJob) return;
    setError(null);
    try {
      const updated = await cancelJob(activeJob.id);
      attachStream(updated);
      void refreshHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel job.");
    }
  };

  const handleRetry = async () => {
    if (!activeJob) return;
    setError(null);
    try {
      const requeued = await retryJob(activeJob.id);
      attachStream(requeued);
      void refreshHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to retry job.");
    }
  };

  const methodOptions = capabilities?.methods ?? DEFAULT_METHODS;
  const basisOptions = capabilities?.basis_sets ?? DEFAULT_BASIS_SETS;
  const engineLabel = capabilities
    ? "Engine: " + capabilities.active_engine
    : "REST + Redis + PostgreSQL";

  return (
    <div className="layout">
      <header className="topbar">
        <div>
          <h1>mshqc-workbench</h1>
          <p className="subtitle">
            Async Compute Queue, SHA-256 Deduplication, Rate Limiting, and Live SSE Telemetry
          </p>
        </div>
        <span className="pill">{engineLabel}</span>
      </header>

      <main className="grid">
        <section className="panel">
          <h2>Job Specification</h2>
          <div className="preset-row">
            {Object.entries(PRESETS).map(([label, xyz]) => (
              <button
                key={label}
                type="button"
                className="btn-secondary"
                onClick={() => setGeometry(xyz)}
              >
                {label}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="form">
            <label>
              Molecular Geometry (XYZ, Angstrom)
              <textarea
                rows={7}
                value={geometry}
                onChange={(e) => setGeometry(e.target.value)}
                spellCheck={false}
                required
              />
            </label>

            <div className="field-row">
              <label>
                Method
                <select value={method} onChange={(e) => setMethod(e.target.value as MethodOption)}>
                  {methodOptions.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Basis Set
                <select value={basis} onChange={(e) => setBasis(e.target.value as BasisOption)}>
                  {basisOptions.map((b) => (
                    <option key={b} value={b}>
                      {b}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="field-row">
              <label>
                Charge
                <input
                  type="number"
                  min={-4}
                  max={4}
                  value={charge}
                  onChange={(e) => setCharge(Number(e.target.value))}
                />
              </label>
              <label>
                Multiplicity
                <input
                  type="number"
                  min={1}
                  max={5}
                  value={multiplicity}
                  onChange={(e) => setMultiplicity(Number(e.target.value))}
                />
              </label>
            </div>

            {error && <div className="alert-error">{error}</div>}

            <button type="submit" className="btn-primary" disabled={submitting}>
              {submitting ? "Submitting..." : "Run Calculation"}
            </button>
          </form>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Execution Telemetry</h2>
            {activeJob && (
              <div className="badges">
                <span className={"badge status-" + activeJob.status}>
                  {activeJob.status.toUpperCase()}
                </span>
                {activeJob.cache_hit && <span className="badge badge-cache">CACHE HIT</span>}
                {activeJob.engine && <span className="badge badge-engine">{activeJob.engine}</span>}
                {(activeJob.status === "queued" || activeJob.status === "running") && (
                  <button type="button" className="btn-danger" onClick={() => void handleCancel()}>
                    Cancel
                  </button>
                )}
                {(activeJob.status === "failed" || activeJob.status === "cancelled") && (
                  <button type="button" className="btn-secondary" onClick={() => void handleRetry()}>
                    Retry
                  </button>
                )}
              </div>
            )}
          </div>

          <MoleculeViewer3D geometryXyz={geometry} />

          {activeJob?.result && (
            <div className="metrics">
              <div>
                <span className="metric-label">Total Energy</span>
                <strong className="mono">{activeJob.result.total_energy_hartree.toFixed(8)} Eh</strong>
              </div>
              <div>
                <span className="metric-label">Nuclear Repulsion</span>
                <strong className="mono">
                  {activeJob.result.nuclear_repulsion_hartree.toFixed(6)} Eh
                </strong>
              </div>
              <div>
                <span className="metric-label">Wall Time</span>
                <strong className="mono">{activeJob.result.wall_time_ms.toFixed(1)} ms</strong>
              </div>
            </div>
          )}

          <ConvergenceChart points={iterations} />

          <div className="terminal" aria-live="polite">
            {iterations.length === 0 ? (
              <span className="muted">Waiting for iteration stream...</span>
            ) : (
              iterations.map((pt) => (
                <div key={pt.iteration} className="log-line">
                  iter={String(pt.iteration).padStart(2, "0")} | E={pt.energy_hartree.toFixed(10)} Eh
                  {" | "}dE={pt.delta_e.toExponential(2)} | t={pt.elapsed_ms.toFixed(1)} ms
                </div>
              ))
            )}
          </div>
        </section>
      </main>

      <section className="panel history-panel">
        <h2>Recent Jobs (Content-Addressed)</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Hash (SHA-256)</th>
                <th>Method / Basis</th>
                <th>Status</th>
                <th>Engine</th>
                <th>Total Energy (Eh)</th>
              </tr>
            </thead>
            <tbody>
              {history.length === 0 ? (
                <tr>
                  <td colSpan={5} className="muted">
                    No jobs submitted yet.
                  </td>
                </tr>
              ) : (
                history.map((job) => (
                  <tr
                    key={job.id}
                    onClick={() => attachStream(job)}
                    className={activeJob?.id === job.id ? "row-active" : ""}
                  >
                    <td className="mono">{job.input_hash.slice(0, 12)}</td>
                    <td>
                      {job.method} / {job.basis}
                    </td>
                    <td>
                      <span className={"badge status-" + job.status}>{job.status}</span>
                    </td>
                    <td className="mono">{job.engine || "-"}</td>
                    <td className="mono">
                      {job.result ? job.result.total_energy_hartree.toFixed(8) : "-"}
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
