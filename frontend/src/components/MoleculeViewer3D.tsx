import { useEffect, useMemo, useRef, useState } from "react";

interface Atom3D {
  symbol: string;
  x: number;
  y: number;
  z: number;
}

const CPK_COLORS: Record<string, string> = {
  H: "#f8fafc", He: "#bae6fd", Li: "#c084fc", Be: "#86efac", B: "#fda4af",
  C: "#334155", N: "#2563eb", O: "#dc2626", F: "#22c55e", Ne: "#7dd3fc",
  Na: "#a855f7", Mg: "#4ade80", Al: "#94a3b8", Si: "#d97706", P: "#f97316",
  S: "#eab308", Cl: "#16a34a", Ar: "#38bdf8", K: "#9333ea", Ca: "#16a34a",
  Ti: "#94a3b8", Cr: "#64748b", Mn: "#7c3aed", Fe: "#c2410c", Co: "#ec4899",
  Ni: "#10b981", Cu: "#b45309", Zn: "#6366f1", Br: "#991b1b", Kr: "#0ea5e9"
};

const COVALENT_RADIUS: Record<string, number> = {
  H: 0.31, He: 0.28, Li: 1.28, Be: 0.96, B: 0.84, C: 0.76, N: 0.71, O: 0.66,
  F: 0.57, Ne: 0.58, Na: 1.66, Mg: 1.41, Al: 1.21, Si: 1.11, P: 1.07, S: 1.05,
  Cl: 1.02, Ar: 1.06, K: 2.03, Ca: 1.76, Sc: 1.70, Ti: 1.60, V: 1.53, Cr: 1.39,
  Mn: 1.39, Fe: 1.32, Co: 1.26, Ni: 1.24, Cu: 1.32, Zn: 1.22, Ga: 1.22, Ge: 1.20,
  As: 1.19, Se: 1.20, Br: 1.20, Kr: 1.16
};

function parseAtoms(raw: string): Atom3D[] {
  const lines = raw
    .trim()
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length === 0) return [];
  const start = /^\d+$/.test(lines[0]) && lines.length >= 3 ? 2 : 0;
  const out: Atom3D[] = [];
  for (let i = start; i < lines.length; i++) {
    const parts = lines[i].split(/\s+/);
    if (parts.length !== 4) continue;
    const x = Number(parts[1]);
    const y = Number(parts[2]);
    const z = Number(parts[3]);
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
    const sym = parts[0].charAt(0).toUpperCase() + parts[0].slice(1).toLowerCase();
    out.push({ symbol: sym, x, y, z });
  }
  return out;
}

interface Props {
  geometryXyz: string;
}

export function MoleculeViewer3D({ geometryXyz }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [rot, setRot] = useState({ yaw: 0.55, pitch: 0.35 });
  const [zoom, setZoom] = useState(1.0);
  const [autoSpin, setAutoSpin] = useState(false);
  const dragRef = useRef<{ active: boolean; x: number; y: number }>({ active: false, x: 0, y: 0 });

  const { centered, bonds, radiusScale } = useMemo(() => {
    const atoms = parseAtoms(geometryXyz);
    if (atoms.length === 0) return { centered: [], bonds: [] as [number, number][], radiusScale: 1 };

    const cx = atoms.reduce((s, a) => s + a.x, 0) / atoms.length;
    const cy = atoms.reduce((s, a) => s + a.y, 0) / atoms.length;
    const cz = atoms.reduce((s, a) => s + a.z, 0) / atoms.length;
    const rel = atoms.map((a) => ({ symbol: a.symbol, x: a.x - cx, y: a.y - cy, z: a.z - cz }));

    let maxDist = 0.8;
    const detectedBonds: [number, number][] = [];
    for (let i = 0; i < rel.length; i++) {
      maxDist = Math.max(maxDist, Math.hypot(rel[i].x, rel[i].y, rel[i].z) + 0.7);
      for (let j = i + 1; j < rel.length; j++) {
        const d = Math.hypot(rel[i].x - rel[j].x, rel[i].y - rel[j].y, rel[i].z - rel[j].z);
        const r1 = COVALENT_RADIUS[rel[i].symbol] ?? 0.9;
        const r2 = COVALENT_RADIUS[rel[j].symbol] ?? 0.9;
        if (d > 0.35 && d <= (r1 + r2) * 1.28) {
          detectedBonds.push([i, j]);
        }
      }
    }
    return { centered: rel, bonds: detectedBonds, radiusScale: maxDist };
  }, [geometryXyz]);

  useEffect(() => {
    if (!autoSpin) return;
    let raf = 0;
    const tick = () => {
      setRot((r) => ({ ...r, yaw: r.yaw + 0.012 }));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [autoSpin]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#0f172a";
    ctx.fillRect(0, 0, w, h);

    if (centered.length === 0) {
      ctx.fillStyle = "#94a3b8";
      ctx.font = "13px ui-monospace, monospace";
      ctx.textAlign = "center";
      ctx.fillText("Enter valid XYZ coordinates to preview 3D geometry", w / 2, h / 2);
      return;
    }

    const cy = Math.cos(rot.yaw);
    const sy = Math.sin(rot.yaw);
    const cp = Math.cos(rot.pitch);
    const sp = Math.sin(rot.pitch);
    const scale = (Math.min(w, h) * 0.37 * zoom) / radiusScale;

    const projected = centered.map((a, idx) => {
      const x1 = a.x * cy - a.z * sy;
      const z1 = a.x * sy + a.z * cy;
      const y2 = a.y * cp - z1 * sp;
      const z2 = a.y * sp + z1 * cp;
      const persp = 4.6 / (4.6 - z2 / Math.max(radiusScale, 1));
      const baseR = (COVALENT_RADIUS[a.symbol] ?? 0.85) * 14 + 5;
      return {
        idx,
        symbol: a.symbol,
        sx: w / 2 + x1 * scale * persp,
        sy: h / 2 - y2 * scale * persp,
        z: z2,
        r: Math.max(7, Math.min(22, baseR * persp * Math.min(zoom, 1.35)))
      };
    });

    type DrawItem =
      | { kind: "bond"; z: number; a: (typeof projected)[number]; b: (typeof projected)[number] }
      | { kind: "atom"; z: number; atom: (typeof projected)[number] };

    const items: DrawItem[] = [];
    for (const [i, j] of bonds) {
      items.push({ kind: "bond", z: (projected[i].z + projected[j].z) / 2, a: projected[i], b: projected[j] });
    }
    for (const atom of projected) {
      items.push({ kind: "atom", z: atom.z, atom });
    }
    items.sort((u, v) => u.z - v.z);

    for (const item of items) {
      if (item.kind === "bond") {
        ctx.strokeStyle = "#64748b";
        ctx.lineWidth = 4;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(item.a.sx, item.a.sy);
        ctx.lineTo(item.b.sx, item.b.sy);
        ctx.stroke();
      } else {
        const { sx, sy: py, r, symbol } = item.atom;
        const fill = CPK_COLORS[symbol] ?? "#38bdf8";
        const grad = ctx.createRadialGradient(sx - r * 0.32, py - r * 0.32, r * 0.12, sx, py, r);
        grad.addColorStop(0, "#ffffff");
        grad.addColorStop(0.35, fill);
        grad.addColorStop(1, "#0f172a");

        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(sx, py, r, 0, Math.PI * 2);
        ctx.fill();

        ctx.strokeStyle = "rgba(15,23,42,0.75)";
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.fillStyle = symbol === "H" || symbol === "S" ? "#0f172a" : "#f8fafc";
        ctx.font = `600 ${Math.max(10, Math.round(r * 0.72))}px Inter, system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(symbol, sx, py + 0.5);
      }
    }
  }, [centered, bonds, rot, zoom, radiusScale]);

  return (
    <div className="viewer-box">
      <div className="viewer-toolbar">
        <span className="muted mono">
          {`3D Preview · ${centered.length} atoms · ${bonds.length} bonds · drag to rotate`}
        </span>
        <div className="viewer-actions">
          <button type="button" className="btn-secondary" onClick={() => setZoom((z) => Math.min(2, +(z + 0.15).toFixed(2)))}>
            +
          </button>
          <button type="button" className="btn-secondary" onClick={() => setZoom((z) => Math.max(0.55, +(z - 0.15).toFixed(2)))}>
            -
          </button>
          <button type="button" className="btn-secondary" onClick={() => setAutoSpin((s) => !s)}>
            {autoSpin ? "Stop" : "Spin"}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setAutoSpin(false);
              setRot({ yaw: 0.55, pitch: 0.35 });
              setZoom(1.0);
            }}
          >
            Reset
          </button>
        </div>
      </div>
      <canvas
        ref={canvasRef}
        width={560}
        height={215}
        className="viewer-canvas"
        onPointerDown={(e) => {
          dragRef.current = { active: true, x: e.clientX, y: e.clientY };
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!dragRef.current.active) return;
          const dx = e.clientX - dragRef.current.x;
          const dy = e.clientY - dragRef.current.y;
          dragRef.current = { active: true, x: e.clientX, y: e.clientY };
          setRot((r) => ({ yaw: r.yaw + dx * 0.012, pitch: r.pitch + dy * 0.012 }));
        }}
        onPointerUp={() => {
          dragRef.current.active = false;
        }}
      />
    </div>
  );
}
