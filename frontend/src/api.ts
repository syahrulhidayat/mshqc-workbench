export type MethodOption =
  | "RHF"
  | "UHF"
  | "ROHF"
  | "RMP2"
  | "UMP2"
  | "ROMP2"
  | "RMP3"
  | "UMP3"
  | "ROMP3"
  | "OMP2"
  | "SCS-OMP2"
  | "SOS-OMP2"
  | "LT-SOS-OMP2"
  | "OMP2.5"
  | "OMP3"
  | "KAPPA-OMP2";

export type EriOption = "auto" | "df" | "cholesky" | "exact";
export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface Capabilities {
  active_engine: string;
  mshqc_installed: boolean;
  mshqc_version: string | null;
  methods: MethodOption[];
  basis_sets: string[];
  ri_basis_sets: string[];
  eri_methods: EriOption[];
  basis_dir: string | null;
  discovered_basis_count: number;
}

export interface IterationPoint {
  iteration: number;
  energy_hartree: number;
  delta_e: number;
  elapsed_ms: number;
  stage?: string;
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
  energy_scf_hartree?: number | null;
  energy_corr_hartree?: number | null;
  energy_mp2_hartree?: number | null;
  energy_mp3_hartree?: number | null;
  s2?: number | null;
  n_basis_functions?: number | null;
  eri_method?: string;
  n_frozen?: number;
}

export interface JobRecord {
  id: string;
  input_hash: string;
  status: JobStatus;
  method: MethodOption;
  basis: string;
  charge: number;
  multiplicity: number;
  eri_method: EriOption;
  freeze_core: boolean;
  geometry_xyz: string;
  engine: string | null;
  result: JobResult | null;
  error_message: string | null;
  cache_hit: boolean;
  created_at: string;
  updated_at: string;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function parseError(res: Response, fallback: string): Promise<Error> {
  const errBody = (await res.json().catch(() => ({}))) as { detail?: string };
  return new Error(errBody.detail || `${fallback} (${res.status})`);
}

export async function fetchCapabilities(): Promise<Capabilities> {
  const res = await fetch(`${API_BASE}/api/v1/capabilities`);
  if (!res.ok) throw await parseError(res, "Failed to fetch capabilities");
  return (await res.json()) as Capabilities;
}

export async function submitJob(payload: {
  geometry_xyz: string;
  method: MethodOption;
  basis: string;
  charge: number;
  multiplicity: number;
  eri_method: EriOption;
  freeze_core: boolean;
}): Promise<JobRecord> {
  const res = await fetch(`${API_BASE}/api/v1/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw await parseError(res, "Request failed");
  return (await res.json()) as JobRecord;
}

export async function cancelJob(jobId: string): Promise<JobRecord> {
  const res = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/cancel`, { method: "POST" });
  if (!res.ok) throw await parseError(res, "Cancel failed");
  return (await res.json()) as JobRecord;
}

export async function retryJob(jobId: string): Promise<JobRecord> {
  const res = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/retry`, { method: "POST" });
  if (!res.ok) throw await parseError(res, "Retry failed");
  return (await res.json()) as JobRecord;
}

export async function fetchJobs(): Promise<JobRecord[]> {
  const res = await fetch(`${API_BASE}/api/v1/jobs?limit=25`);
  if (!res.ok) throw await parseError(res, "Failed to fetch job history");
  return (await res.json()) as JobRecord[];
}

export function subscribeJobEvents(
  jobId: string,
  handlers: {
    onIteration: (point: IterationPoint) => void;
    onDone: (payload: {
      status: JobStatus;
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
