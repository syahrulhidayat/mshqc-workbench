import type { IterationPoint } from "../api";

interface Props {
  points: IterationPoint[];
}

export function ConvergenceChart({ points }: Props) {
  if (points.length === 0) {
    return <div className="chart-empty">Run a calculation to stream SCF / OMP convergence.</div>;
  }

  const width = 560;
  const height = 175;
  const padL = 78;
  const padR = 18;
  const padT = 18;
  const padB = 28;

  const energies = points.map((p) => p.energy_hartree);
  const minE = Math.min(...energies);
  const maxE = Math.max(...energies);
  const spanE = Math.max(maxE - minE, 1e-9);

  const xScale = (idx: number) =>
    points.length === 1
      ? padL + (width - padL - padR) / 2
      : padL + (idx / (points.length - 1)) * (width - padL - padR);

  const yScale = (e: number) => padT + ((maxE - e) / spanE) * (height - padT - padB);

  const polyline = points.map((p, i) => `${xScale(i).toFixed(1)},${yScale(p.energy_hartree).toFixed(1)}`).join(" ");
  const midE = (maxE + minE) / 2;

  return (
    <svg
      className="convergence-svg"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="SCF and Orbital-Optimized energy convergence curve"
    >
      {[maxE, midE, minE].map((val, idx) => {
        const y = yScale(val);
        return (
          <g key={idx}>
            <line x1={padL} y1={y} x2={width - padR} y2={y} stroke="#e2e8f0" strokeDasharray="3 3" />
            <text x={padL - 8} y={y + 3} textAnchor="end" className="axis-label">
              {val.toFixed(6)}
            </text>
          </g>
        );
      })}

      <polyline fill="none" stroke="#0284c7" strokeWidth="2.2" points={polyline} />

      {points.map((p, i) => {
        const stage = (p.stage || "scf").toLowerCase();
        const fill = stage === "omp" ? "#0d9488" : stage === "mp2" || stage === "mp3" ? "#d97706" : "#0284c7";
        return (
          <circle
            key={`${p.iteration}-${i}`}
            cx={xScale(i)}
            cy={yScale(p.energy_hartree)}
            r={3.5}
            fill={fill}
          >
            <title>{`[${stage.toUpperCase()}] Iter ${p.iteration}: ${p.energy_hartree.toFixed(8)} Ha`}</title>
          </circle>
        );
      })}

      <text x={padL} y={height - 8} className="axis-label">
        Iter 1
      </text>
      <text x={width - padR} y={height - 8} textAnchor="end" className="axis-label">
        {`Iter ${points[points.length - 1].iteration} (${points[points.length - 1].energy_hartree.toFixed(6)} Ha)`}
      </text>
    </svg>
  );
}
