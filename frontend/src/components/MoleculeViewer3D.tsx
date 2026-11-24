import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";

interface AtomPoint {
  symbol: string;
  x: number;
  y: number;
  z: number;
}

const CPK_COLORS: Record<string, string> = {
  H: "#f1f5f9",
  C: "#334155",
  N: "#2563eb",
  O: "#dc2626",
  F: "#16a34a",
  P: "#ea580c",
  S: "#ca8a04",
  Cl: "#15803d"
};

const COVALENT_RADII: Record<string, number> = {
  H: 0.31,
  He: 0.28,
  Li: 1.28,
  Be: 0.96,
  B: 0.84,
  C: 0.76,
  N: 0.71,
  O: 0.66,
  F: 0.57,
  Na: 1.66,
  Mg: 1.41,
  Al: 1.21,
  Si: 1.11,
  P: 1.07,
  S: 1.05,
  Cl: 1.02
};

function parsePreviewAtoms(rawXyz: string): AtomPoint[] {
  const lines = rawXyz
    .trim()
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length === 0) return [];

  const coordLines = /^\d+$/.test(lines[0]) ? lines.slice(2) : lines;
  const atoms: AtomPoint[] = [];

  for (const line of coordLines) {
    const parts = line.split(/\s+/);
    if (parts.length !== 4) continue;
    const x = Number(parts[1]);
    const y = Number(parts[2]);
    const z = Number(parts[3]);
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
    const sym = parts[0][0].toUpperCase() + parts[0].slice(1).toLowerCase();
    atoms.push({ symbol: sym, x, y, z });
  }
  return atoms;
}

interface Props {
  geometryXyz: string;
}

export function MoleculeViewer3D({ geometryXyz }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [angles, setAngles] = useState({ yaw: 0.45, pitch: 0.35 });
  const [zoom, setZoom] = useState(1.0);
  const dragRef = useRef<{ x: number; y: number } | null>(null);

  const atoms = useMemo(() => parsePreviewAtoms(geometryXyz), [geometryXyz]);

  const bonds = useMemo(() => {
    const pairs: Array<[number, number]> = [];
    for (let i = 0; i < atoms.length; i += 1) {
      const ri = COVALENT_RADII[atoms[i].symbol] ?? 0.8;
      for (let j = i + 1; j < atoms.length; j += 1) {
        const rj = COVALENT_RADII[atoms[j].symbol] ?? 0.8;
        const dx = atoms[i].x - atoms[j].x;
        const dy = atoms[i].y - atoms[j].y;
        const dz = atoms[i].z - atoms[j].z;
        const dist = Math.hypot(dx, dy, dz);
        if (dist > 0.2 && dist <= (ri + rj) * 1.3) {
          pairs.push([i, j]);
        }
      }
    }
    return pairs;
  }, [atoms]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#0f172a";
    ctx.fillRect(0, 0, width, height);

    if (atoms.length === 0) {
      ctx.fillStyle = "#94a3b8";
      ctx.font = "13px ui-monospace, monospace";
      ctx.textAlign = "center";
      ctx.fillText("Enter valid XYZ coordinates to preview 3D geometry", width / 2, height / 2);
      return;
    }

    const cx = atoms.reduce((acc, a) => acc + a.x, 0) / atoms.length;
    const cy = atoms.reduce((acc, a) => acc + a.y, 0) / atoms.length;
    const cz = atoms.reduce((acc, a) => acc + a.z, 0) / atoms.length;

    const centered = atoms.map((a) => ({
      symbol: a.symbol,
      x: a.x - cx,
      y: a.y - cy,
      z: a.z - cz
    }));

    const maxR = Math.max(
      1.0,
      ...centered.map((a) => Math.hypot(a.x, a.y, a.z))
    );
    const scale = ((Math.min(width, height) * 0.34) / maxR) * zoom;

    const cosY = Math.cos(angles.yaw);
    const sinY = Math.sin(angles.yaw);
    const cosP = Math.cos(angles.pitch);
    const sinP = Math.sin(angles.pitch);

    const projected = centered.map((a, idx) => {
      const rx = a.x * cosY + a.z * sinY;
      const rz1 = -a.x * sinY + a.z * cosY;
      const ry = a.y * cosP - rz1 * sinP;
      const rz = a.y * sinP + rz1 * cosP;
      const persp = 5.5 / Math.max(1.5, 5.5 - rz / maxR);
      return {
        idx,
        symbol: a.symbol,
        sx: width / 2 + rx * scale * persp,
        sy: height / 2 - ry * scale * persp,
        depth: rz,
        persp
      };
    });

    for (const [i, j] of bonds) {
      const a = projected[i];
      const b = projected[j];
      ctx.strokeStyle = "#94a3b8";
      ctx.lineWidth = Math.max(2, 3.2 * ((a.persp + b.persp) / 2));
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
    }

    const sorted = [...projected].sort((a, b) => a.depth - b.depth);
    for (const pt of sorted) {
      const baseR = pt.symbol === "H" ? 10 : 15;
      const radius = Math.max(6, baseR * pt.persp * Math.min(zoom, 1.35));
      const color = CPK_COLORS[pt.symbol] ?? "#38bdf8";

      ctx.beginPath();
      ctx.arc(pt.sx, pt.sy, radius, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "#020617";
      ctx.stroke();

      ctx.fillStyle = pt.symbol === "H" ? "#0f172a" : "#f8fafc";
      ctx.font = "bold 11px ui-monospace, monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(pt.symbol, pt.sx, pt.sy);
    }
  }, [atoms, bonds, angles, zoom]);

  const onPointerDown = (e: PointerEvent<HTMLCanvasElement>) => {
    dragRef.current = { x: e.clientX, y: e.clientY };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: PointerEvent<HTMLCanvasElement>) => {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.x;
    const dy = e.clientY - dragRef.current.y;
    dragRef.current = { x: e.clientX, y: e.clientY };
    setAngles((prev) => ({
      yaw: prev.yaw + dx * 0.012,
      pitch: Math.max(-1.4, Math.min(1.4, prev.pitch + dy * 0.012))
    }));
  };

  const onPointerUp = () => {
    dragRef.current = null;
  };

  return (
    <div className="viewer-box">
      <div className="viewer-toolbar">
        <span className="muted mono">
          3D Geometry ({atoms.length} atoms, {bonds.length} bonds) — Drag to rotate
        </span>
        <div className="viewer-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setZoom((z) => Math.min(2.2, Number((z + 0.15).toFixed(2))))}
          >
            +
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setZoom((z) => Math.max(0.5, Number((z - 0.15).toFixed(2))))}
          >
            -
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setAngles({ yaw: 0.45, pitch: 0.35 });
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
        height={220}
        className="viewer-canvas"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      />
    </div>
  );
}
