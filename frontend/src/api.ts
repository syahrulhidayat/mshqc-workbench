export type MethodOption = "RHF" | "RMP2" | "OMP2";
export type BasisOption = "sto-3g" | "6-31g" | "cc-pvdz";

export interface IterationPoint {
  iteration: number;
  energy_hartree: number;
  delta_e: number;
  elapsed_ms: number;
}

export interface JobResult {
  engine: string;
  method: string;
  basis: string;
  nuclear_repulsion_hartree: number;
  total_energy_hartree: number;
  converged: boolean;
  iterations: IterationPoint[];
  wall_time_ms: number;
}

export interface JobRecord {
  id: string;
  input_hash: string;
  status: "queued" | "running" | "completed" | "failed";
  method: MethodOption;
  basis: BasisOption;
  charge: number;
  multiplicity: number;
  geometry_xyz: string;
  engine: string | null;
  result: JobResult | null;
  error_message: string | null;
  cache_hit: boolean;
  created_at: string;
  updated_at: string;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export async function submitJob(payload: {
  geometry_xyz: string;
  method: MethodOption;
  basis: BasisOption;
  charge: number;
  multiplicity: number;
}): Promise<JobRecord> {
  const res = await fetch(`${API_BASE}/api/v1/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (!res.ok) {
    const errBody = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(errBody.detail || `Request failed (${res.status})`); } return (await res.json()) as JobRecord; }  export async function fetchJobs(): Promise<JobRecord[]> { const res = await fetch(`${API_BASE}/api/v1/jobs?limit=20`);
  if (!res.ok) {
    throw new Error(`Failed to fetch job history (${res.status})`);
  }
  return (await res.json()) as JobRecord[];
}

export function subscribeJobEvents(
  jobId: string,
  handlers: {
    onIteration: (point: IterationPoint) => void;
    onDone: (payload: {
      status: JobRecord["status"];
      engine: string | null;
      result: JobResult | null;
      error_message: string | null;
    }) => void;
    onError: () => void;
  }
): () => void {
  const source = new EventSource(`${API_BASE}/api/v1/jobs/${jobId}/events`);

  source.addEventListener("iteration", (evt) => {
    const data = JSON.parse((evt as MessageEvent).data) as IterationPoint;
    handlers.onIteration(data);
  });

  source.addEventListener("done", (evt) => {
    const data = JSON.parse((evt as MessageEvent).data);
    handlers.onDone(data);
    source.close();
  });

  source.onerror = () => {
    handlers.onError();
    source.close();
  };

  return () => source.close();
}
