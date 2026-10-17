import { formatInt } from "@/lib/format";

interface SparklineProps {
  /** Oldest first. */
  values: readonly number[];
  width?: number;
  height?: number;
  /** What one value counts, for the accessible name. */
  label?: string;
}

const PAD = 3;

/**
 * A 2 px line over the item's last checks, scaled to its own range so the
 * shape reads; the end point is marked. Values live in the accessible name.
 */
export function Sparkline({
  values,
  width = 112,
  height = 28,
  label = "Active ads",
}: SparklineProps) {
  const name =
    values.length === 0
      ? `${label}: no checks yet`
      : `${label} over the last ${values.length} ${values.length === 1 ? "check" : "checks"}: ${values.map(formatInt).join(", ")}`;

  if (values.length === 0) {
    return (
      <svg
        role="img"
        aria-label={name}
        data-points={0}
        width={width}
        height={height}
        className="block"
      >
        <line
          x1={0}
          x2={width}
          y1={height - PAD}
          y2={height - PAD}
          stroke="var(--rule)"
          strokeDasharray="2 3"
        />
      </svg>
    );
  }

  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo;
  const step = values.length > 1 ? (width - PAD * 2) / (values.length - 1) : 0;
  const x = (i: number) => (values.length > 1 ? PAD + i * step : width / 2);
  // A flat series sits mid-height rather than on the floor.
  const y = (v: number) =>
    span === 0 ? height / 2 : PAD + (1 - (v - lo) / span) * (height - PAD * 2);
  const d = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  const last = values.length - 1;

  return (
    <svg
      role="img"
      aria-label={name}
      data-points={values.length}
      width={width}
      height={height}
      className="block overflow-visible"
    >
      {values.length > 1 ? (
        <path
          d={d.join(" ")}
          fill="none"
          stroke="var(--signal)"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ) : null}
      <circle
        cx={x(last)}
        cy={y(values[last] ?? 0)}
        r={3}
        fill="var(--signal)"
        stroke="var(--panel)"
        strokeWidth={2}
      />
    </svg>
  );
}
