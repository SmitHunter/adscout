import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import type { TrendPoint } from "@/api/client";
import { formatDayMonth, formatInt } from "@/lib/format";

import { checkLabel } from "./watch";

/**
 * The watch item's history as four small multiples on one shared x-axis: active
 * ads, new creatives per check, advertisers and Winner Score. One crosshair runs
 * through all four; the readout column on the left shows the values at it (the
 * latest check until you point at another). Capped checks are floors, drawn as
 * hollow dots.
 */

export const CAPPED_COPY = "At the search limit";

type MetricKey = "active_ads" | "new_creatives" | "advertisers" | "winner_score";

interface Metric {
  key: MetricKey;
  title: string;
  form: "line" | "bar";
}

const METRICS: Metric[] = [
  { key: "active_ads", title: "Active ads", form: "line" },
  { key: "new_creatives", title: "New creatives per check", form: "bar" },
  { key: "advertisers", title: "Advertisers", form: "line" },
  { key: "winner_score", title: "Winner Score", form: "line" },
];

const ROW_HEIGHT = 112;
const AXIS_HEIGHT = 28;
const PAD_TOP = 14;
const PAD_BOTTOM = 10;
/** Room on the right for the y labels, so they never sit under the latest point. */
const Y_LABEL_WIDTH = 40;
const DEFAULT_WIDTH = 720;
const MIN_LABEL_GAP = 64;

/** 1, 2, 2.5, 5 × 10^n: the smallest of those at or above `n`. */
function niceCeil(n: number): number {
  if (n <= 0) return 1;
  const exp = 10 ** Math.floor(Math.log10(n));
  for (const step of [1, 1.2, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    if (step * exp >= n) return step * exp;
  }
  return 10 * exp;
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (el === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width;
      if (next !== undefined && next > 0) setWidth(Math.round(next));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width || DEFAULT_WIDTH];
}

interface Scale {
  max: number;
  ticks: number[];
  /** The first check's new creatives are every creative it saw: kept off the scale. */
  overflowFirst: boolean;
}

function scaleFor(metric: Metric, points: TrendPoint[]): Scale {
  if (metric.key === "winner_score")
    return { max: 100, ticks: [40, 70, 100], overflowFirst: false };
  const values = points.map((p) => p[metric.key]);
  let overflowFirst = false;
  let peak = Math.max(...values, 0);
  if (metric.key === "new_creatives" && values.length > 2) {
    const rest = Math.max(...values.slice(1), 1);
    if ((values[0] ?? 0) > rest * 2) {
      overflowFirst = true;
      peak = rest;
    }
  }
  const max = niceCeil(peak * 1.1);
  // A half-way tick only when it is a whole count.
  return { max, ticks: Number.isInteger(max / 2) ? [max / 2, max] : [max], overflowFirst };
}

export function TrendCharts({ points, timezone }: { points: TrendPoint[]; timezone?: string }) {
  const [plotRef, width] = useWidth<HTMLDivElement>();
  const [selected, setSelected] = useState<number | null>(null);
  const n = points.length;
  const last = n - 1;
  const index = selected !== null && selected <= last ? selected : last;
  const plotWidth = Math.max(width - Y_LABEL_WIDTH, 40);
  const band = plotWidth / Math.max(n, 1);
  const cx = (i: number) => i * band + band / 2;

  const pick = (clientX: number, el: HTMLElement) => {
    const x = clientX - el.getBoundingClientRect().left;
    setSelected(Math.min(Math.max(Math.floor(x / band), 0), last));
  };

  const onKeyDown = (event: KeyboardEvent) => {
    const moves: Record<string, number> = {
      ArrowLeft: Math.max(index - 1, 0),
      ArrowRight: Math.min(index + 1, last),
      Home: 0,
      End: last,
    };
    if (event.key in moves) {
      event.preventDefault();
      setSelected(moves[event.key] ?? last);
    } else if (event.key === "Escape") {
      setSelected(null);
    }
  };

  if (n === 0) {
    return (
      <p className="border border-rule bg-panel px-5 py-8 text-13 text-muted md:px-6">
        The trend starts after the first check.
      </p>
    );
  }

  const point = points[index] as TrendPoint;
  const previous = index > 0 ? points[index - 1] : undefined;
  const label = checkLabel(points, index, timezone);
  const withTime = label !== formatDayMonth(point.date);

  return (
    <div
      role="group"
      aria-label="Trend charts. Use the arrow keys to step through checks."
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="grid grid-cols-[148px_minmax(0,1fr)] focus-visible:outline-offset-[-2px] md:grid-cols-[188px_minmax(0,1fr)]"
    >
      <div data-testid="trend-readout" className="border-r border-rule" aria-live="polite">
        {METRICS.map((metric) => (
          <Readout
            key={metric.key}
            metric={metric}
            point={point}
            previous={previous}
            first={index === 0}
          />
        ))}
        <div className="flex items-center gap-2 px-4 md:px-5" style={{ height: AXIS_HEIGHT }}>
          <span className="num text-12 whitespace-nowrap text-text">{label}</span>
          <span className="text-12 whitespace-nowrap text-muted">
            {/* A same-day label carries a time: shorter words keep it on one line. */}
            {withTime
              ? index === last
                ? "Latest"
                : `${index + 1} of ${n}`
              : index === last
                ? "Latest check"
                : `Check ${index + 1} of ${n}`}
          </span>
        </div>
      </div>

      <div
        ref={plotRef}
        className="min-w-0 cursor-crosshair touch-none"
        aria-hidden="true"
        onPointerMove={(e) => pick(e.clientX, e.currentTarget)}
        onPointerDown={(e) => pick(e.clientX, e.currentTarget)}
        onPointerLeave={(e) => {
          if (e.pointerType === "mouse") setSelected(null);
        }}
      >
        {METRICS.map((metric) => (
          <Plot
            key={metric.key}
            metric={metric}
            points={points}
            width={width}
            plotWidth={plotWidth}
            band={band}
            cx={cx}
            index={index}
          />
        ))}
        <XAxis points={points} width={width} plotWidth={plotWidth} cx={cx} index={index} />
      </div>

      <TrendTable points={points} timezone={timezone} />
    </div>
  );
}

function Readout({
  metric,
  point,
  previous,
  first,
}: {
  metric: Metric;
  point: TrendPoint;
  previous: TrendPoint | undefined;
  first: boolean;
}) {
  const value = point[metric.key];
  const capped = metric.key === "active_ads" && point.capped;
  let note: ReactNode;
  if (capped) {
    note = (
      <span className="inline-flex items-center gap-1.5 text-text">
        <HollowKey />
        {CAPPED_COPY}
      </span>
    );
  } else if (metric.key === "new_creatives" && first) {
    note = "First check counts every creative";
  } else if (previous === undefined) {
    note = "First check";
  } else {
    const delta = value - previous[metric.key];
    note = (
      <>
        <span className="num">
          {delta > 0 ? "+" : delta < 0 ? "−" : "±"}
          {formatInt(Math.abs(delta))}
        </span>{" "}
        vs previous
      </>
    );
  }

  return (
    <section
      className="flex flex-col justify-center border-b border-rule px-4 md:px-5"
      style={{ height: ROW_HEIGHT }}
    >
      <h3 className="text-13 text-muted">{metric.title}</h3>
      <p className="num mt-1 text-24 leading-none font-medium tracking-[-0.03em] text-text">
        {formatInt(value)}
        {capped ? <span className="text-muted">+</span> : null}
      </p>
      <p className="mt-1.5 text-12 text-muted">{note}</p>
    </section>
  );
}

/** The legend mark for a capped check: the hollow dot, as drawn on the chart. */
export function HollowKey() {
  return (
    <svg width={10} height={10} aria-hidden="true" className="shrink-0">
      <circle cx={5} cy={5} r={3.5} fill="var(--panel)" stroke="var(--signal)" strokeWidth={2} />
    </svg>
  );
}

function Plot({
  metric,
  points,
  width,
  plotWidth,
  band,
  cx,
  index,
}: {
  metric: Metric;
  points: TrendPoint[];
  width: number;
  plotWidth: number;
  band: number;
  cx: (i: number) => number;
  index: number;
}) {
  const scale = scaleFor(metric, points);
  const bottom = ROW_HEIGHT - PAD_BOTTOM;
  const y = (v: number) => bottom - (Math.min(v, scale.max) / scale.max) * (bottom - PAD_TOP);
  const values = points.map((p) => p[metric.key]);
  const selectedX = cx(index);

  return (
    <svg
      width={width}
      height={ROW_HEIGHT}
      className="block border-b border-rule"
      data-metric={metric.key}
    >
      {/* Recessive grid: a baseline and labelled ticks on the right. */}
      {scale.ticks.map((tick) => (
        <g key={tick}>
          <line
            x1={0}
            x2={plotWidth}
            y1={y(tick)}
            y2={y(tick)}
            stroke="var(--rule)"
            strokeDasharray={metric.key === "winner_score" ? "3 3" : undefined}
          />
          <text
            x={plotWidth + 8}
            y={y(tick) + 4}
            fill="var(--muted)"
            fontSize={11}
            fontFamily="var(--ff-num)"
          >
            {formatInt(tick)}
          </text>
        </g>
      ))}
      <line x1={0} x2={plotWidth} y1={bottom} y2={bottom} stroke="var(--rule)" />

      {/* The crosshair, shared by all four rows. */}
      <line
        x1={selectedX}
        x2={selectedX}
        y1={4}
        y2={ROW_HEIGHT}
        stroke="var(--muted)"
        strokeOpacity={0.6}
        strokeDasharray="2 3"
      />

      {metric.form === "bar" ? (
        <Bars
          values={values}
          band={band}
          cx={cx}
          y={y}
          bottom={bottom}
          index={index}
          overflowFirst={scale.overflowFirst}
          top={PAD_TOP}
        />
      ) : (
        <Line metric={metric} points={points} values={values} cx={cx} y={y} index={index} />
      )}
    </svg>
  );
}

function Line({
  metric,
  points,
  values,
  cx,
  y,
  index,
}: {
  metric: Metric;
  points: TrendPoint[];
  values: number[];
  cx: (i: number) => number;
  y: (v: number) => number;
  index: number;
}) {
  const d = values
    .map((v, i) => `${i === 0 ? "M" : "L"}${cx(i).toFixed(1)},${y(v).toFixed(1)}`)
    .join(" ");
  const showCapped = metric.key === "active_ads";
  const selectedCapped = showCapped && points[index]?.capped === true;
  const selectedValue = values[index] ?? 0;

  return (
    <g>
      {values.length > 1 ? (
        <path
          d={d}
          fill="none"
          stroke="var(--signal)"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ) : null}
      {showCapped
        ? points.map((p, i) =>
            p.capped ? (
              <circle
                key={p.snapshot_id}
                data-capped="true"
                cx={cx(i)}
                cy={y(p.active_ads)}
                r={i === index ? 5 : 4}
                fill="var(--panel)"
                stroke="var(--signal)"
                strokeWidth={2}
              >
                <title>{CAPPED_COPY}</title>
              </circle>
            ) : null,
          )
        : null}
      {selectedCapped ? null : (
        <circle
          cx={cx(index)}
          cy={y(selectedValue)}
          r={4}
          fill="var(--signal)"
          stroke="var(--panel)"
          strokeWidth={2}
        />
      )}
    </g>
  );
}

function Bars({
  values,
  band,
  cx,
  y,
  bottom,
  top,
  index,
  overflowFirst,
}: {
  values: number[];
  band: number;
  cx: (i: number) => number;
  y: (v: number) => number;
  bottom: number;
  top: number;
  index: number;
  overflowFirst: boolean;
}) {
  const w = Math.max(Math.min(band * 0.56, 20), 2);
  const r = Math.min(4, w / 2);
  return (
    <g>
      {values.map((v, i) => {
        if (v <= 0) return null;
        const x = cx(i) - w / 2;
        const overflow = overflowFirst && i === 0;
        const t = overflow ? top - 4 : y(v);
        const h = bottom - t;
        const rr = Math.min(r, h);
        const d = `M${x},${bottom} V${t + rr} Q${x},${t} ${x + rr},${t} H${x + w - rr} Q${x + w},${t} ${x + w},${t + rr} V${bottom} Z`;
        return (
          <g key={i}>
            <path
              d={d}
              fill={overflow ? "var(--muted)" : "var(--signal)"}
              fillOpacity={overflow ? 0.55 : i === index ? 1 : 0.8}
            />
            {overflow ? (
              // A break: the first check's bar runs past the scale.
              <path
                d={`M${x - 1},${t + 12} L${x + w + 1},${t + 7} M${x - 1},${t + 16} L${x + w + 1},${t + 11}`}
                stroke="var(--panel)"
                strokeWidth={2}
              />
            ) : null}
          </g>
        );
      })}
    </g>
  );
}

function XAxis({
  points,
  width,
  plotWidth,
  cx,
  index,
}: {
  points: TrendPoint[];
  width: number;
  plotWidth: number;
  cx: (i: number) => number;
  index: number;
}) {
  const n = points.length;
  const stride = Math.max(1, Math.ceil((MIN_LABEL_GAP * n) / Math.max(plotWidth, 1)));
  const selectedX = cx(index);
  // Labels too close to the selected date's flag step aside for it.
  const labelled = points
    .map((p, i) => ({ p, i }))
    .filter(({ i }) => (n - 1 - i) % stride === 0)
    .filter(({ i }) => i !== index && Math.abs(cx(i) - selectedX) >= MIN_LABEL_GAP * 0.8);
  // Labels are ~44 px wide: keep each inside the plot.
  const clampX = (x: number) => Math.min(Math.max(x, 26), plotWidth - 26);

  return (
    <svg width={width} height={AXIS_HEIGHT} data-axis="x" className="block">
      {labelled.map(({ p, i }) => (
        <text
          key={p.snapshot_id}
          x={clampX(cx(i))}
          y={17}
          textAnchor="middle"
          fill="var(--muted)"
          fontSize={11}
          fontFamily="var(--ff-num)"
        >
          {formatDayMonth(p.date)}
        </text>
      ))}
      <g>
        <rect x={clampX(selectedX) - 26} y={4} width={52} height={18} fill="var(--rule)" />
        <text
          x={clampX(selectedX)}
          y={17}
          textAnchor="middle"
          fill="var(--text)"
          fontSize={11}
          fontFamily="var(--ff-num)"
        >
          {formatDayMonth(points[index]?.date ?? "")}
        </text>
      </g>
    </svg>
  );
}

/** The four series for screen readers and anyone who'd rather read numbers. */
function TrendTable({ points, timezone }: { points: TrendPoint[]; timezone?: string }) {
  return (
    <table className="sr-only">
      <caption>Checks, oldest first</caption>
      <thead>
        <tr>
          <th scope="col">Date</th>
          {METRICS.map((m) => (
            <th key={m.key} scope="col">
              {m.title}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {points.map((p, i) => (
          <tr key={p.snapshot_id}>
            <th scope="row">{checkLabel(points, i, timezone)}</th>
            <td>
              {p.active_ads}
              {p.capped ? `, ${CAPPED_COPY.toLowerCase()}` : ""}
            </td>
            <td>{p.new_creatives}</td>
            <td>{p.advertisers}</td>
            <td>{p.winner_score}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
