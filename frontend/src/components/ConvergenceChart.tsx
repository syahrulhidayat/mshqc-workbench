import type { IterationPoint } from "../api";

interface Props {
  points: IterationPoint[];
}

export function ConvergenceChart({ points }: Props) {
  if (points.length === 0) {
    return (
      <div className="chart-empty">
        Submit or select a job to inspect iteration energy convergence.
      </div>
    );
  }

  const width = 560;
  const height = 210;
  const padLeft = 72;
  const padRight = 20;
  const padTop = 18;
  const padBottom = 30;

  const energies = points.map((p) => p.energy_hartree);
  const minE = Math.min(...energies);
  const maxE = Math.max(...energies);
  const spanE = maxE - minE || 1e-6;

  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const toX = (idx: number) =>
    points.length === 1 ? padLeft + plotW / 2 : padLeft + (idx / (points.length - 1)) * plotW;
  const toY = (e: number) => padTop + ((maxE - e) / spanE) * plotH;

  const polyline = points
    .map((p, i) => `${toX(i).toFixed(1)},${toY(p.energy_hartree).toFixed(1)}`)
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${width}${height}`}
      className="convergence-svg"
      role="img"
      aria-label="Energy convergence plot"
    >
      <line x1={padLeft} y1={padTop} x2={padLeft} y2={height - padBottom} stroke="#cbd5e1" />
      <line
        x1={padLeft}
        y1={height - padBottom}
        x2={width - padRight}
        y2={height - padBottom}
        stroke="#cbd5e1"
      />

      <text x={padLeft - 8} y={padTop + 4} textAnchor="end" className="axis-label">
        {maxE.toFixed(4)}
      </text>
      <text x={padLeft - 8} y={height - padBottom} textAnchor="end" className="axis-label">
        {minE.toFixed(4)}
      </text>
      <text x={padLeft + plotW / 2} y={height - 6} textAnchor="middle" className="axis-label">
        Iteration (E in Hartree)
      </text>

      <polyline fill="none" stroke="#0284c7" strokeWidth="2.2" points={polyline} />
      {points.map((p, i) => (
        <circle key={p.iteration} cx={toX(i)} cy={toY(p.energy_hartree)} r="3.2" fill="#0f172a" />
      ))}
    </svg>
  );
}
