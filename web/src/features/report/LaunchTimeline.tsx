import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { Schemas } from "@/api/client";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatInt } from "@/lib/format";

import {
  axisTicks,
  formatBucketRange,
  hasLaunches,
  toSeries,
  type Granularity,
  type TimelineRow,
} from "./timeline";

type TimelineBucket = Schemas["TimelineBucketView"];

const CHART_HEIGHT = 220;
const MARGIN = { top: 8, right: 4, bottom: 0, left: 0 };
const X_AXIS_HEIGHT = 28;
/**
 * The other advertisers' series: --muted, behind the leader's --signal but at
 * least 3:1 against the panel in both themes (3.5:1 dark, 3.3:1 light).
 */
const OTHERS_OPACITY = 0.75;
const GAP = 2;
const RADIUS = 4;

interface LaunchTimelineProps {
  month: TimelineBucket[];
  week: TimelineBucket[];
  /** The leading advertiser, whose launches stack in --signal; null when none leads. */
  leaderName: string | null;
}

export function LaunchTimeline({ month, week, leaderName }: LaunchTimelineProps) {
  const [granularity, setGranularity] = useState<Granularity>("month");
  const buckets = granularity === "month" ? month : week;
  const rows = useMemo(() => toSeries(buckets, granularity), [buckets, granularity]);
  const [chartWidth, setChartWidth] = useState(0);
  // Quarter labels need ~72 px each; narrower charts label half-years.
  const quarterPx = (chartWidth / Math.max(rows.length, 1)) * (granularity === "month" ? 3 : 13);
  const everyMonths = chartWidth > 0 && quarterPx < 72 ? 6 : 3;
  const ticks = useMemo(() => axisTicks(rows, everyMonths), [rows, everyMonths]);
  const empty = !hasLaunches(month) && !hasLaunches(week);
  const caption = `New creatives per ${granularity}`;
  const othersLabel = leaderName === null ? "All advertisers" : "Other advertisers";

  return (
    <section aria-labelledby="timeline-heading" className="border border-rule bg-panel">
      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-5 pt-4 md:px-6">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <h2 id="timeline-heading" className="text-15 font-medium text-text">
            {caption}
          </h2>
          {!empty ? (
            <ul className="flex items-center gap-4 text-12 text-muted" aria-label="Legend">
              {leaderName !== null ? (
                <li className="flex items-center gap-1.5">
                  <span className="size-2.5 rounded-[1px] bg-signal" aria-hidden="true" />
                  <span>{leaderName}</span>
                </li>
              ) : null}
              <li className="flex items-center gap-1.5">
                <span
                  className="size-2.5 rounded-[1px] bg-muted"
                  style={{ opacity: OTHERS_OPACITY }}
                  aria-hidden="true"
                />
                <span>{othersLabel}</span>
              </li>
            </ul>
          ) : null}
        </div>
        {!empty ? (
          <ToggleGroup
            type="single"
            size="sm"
            value={granularity}
            onValueChange={(next) => {
              if (next === "month" || next === "week") setGranularity(next);
            }}
            aria-label="Bucket size"
          >
            <ToggleGroupItem value="month">Month</ToggleGroupItem>
            <ToggleGroupItem value="week">Week</ToggleGroupItem>
          </ToggleGroup>
        ) : null}
      </header>

      {empty ? (
        <p className="px-5 pt-2 pb-5 text-13 text-muted md:px-6">No launch dates yet.</p>
      ) : (
        <>
          <div className="px-3 pt-3 pb-3 md:px-4" aria-hidden="true">
            <ResponsiveContainer
              onResize={(width) => setChartWidth(width)}
              width="100%"
              height={CHART_HEIGHT}
              initialDimension={{ width: 1, height: CHART_HEIGHT }}
            >
              {/* Hidden from assistive tech (the table carries it), so out of the tab order. */}
              <BarChart data={rows} margin={MARGIN} barCategoryGap="22%" accessibilityLayer={false}>
                <CartesianGrid vertical={false} stroke="var(--rule)" strokeWidth={1} />
                <XAxis
                  dataKey="start"
                  ticks={ticks.map((tick) => tick.start)}
                  interval={0}
                  height={X_AXIS_HEIGHT}
                  tickLine={false}
                  axisLine={{ stroke: "var(--rule)" }}
                  tick={(props: AxisTickProps) => <YearTick {...props} ticks={ticks} />}
                />
                <YAxis
                  allowDecimals={false}
                  width={36}
                  tickLine={false}
                  axisLine={false}
                  tickMargin={6}
                  tick={{ fill: "var(--muted)", fontSize: 12, fontFamily: "var(--ff-num)" }}
                />
                <Tooltip
                  cursor={{ fill: "var(--rule)", fillOpacity: 0.45 }}
                  isAnimationActive={false}
                  content={(props) => (
                    <TimelineTooltip {...props} leaderName={leaderName} othersLabel={othersLabel} />
                  )}
                />
                <Bar
                  dataKey="leader"
                  stackId="launches"
                  fill="var(--signal)"
                  maxBarSize={24}
                  isAnimationActive={false}
                  shape={(props: unknown) => <Segment {...(props as SegmentProps)} part="leader" />}
                />
                <Bar
                  dataKey="others"
                  stackId="launches"
                  fill="var(--muted)"
                  fillOpacity={OTHERS_OPACITY}
                  maxBarSize={24}
                  isAnimationActive={false}
                  shape={(props: unknown) => <Segment {...(props as SegmentProps)} part="others" />}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <TimelineTable
            caption={caption}
            rows={rows}
            leaderName={leaderName}
            othersLabel={othersLabel}
          />
        </>
      )}
    </section>
  );
}

interface SegmentProps {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  fill?: string;
  fillOpacity?: number;
  payload?: TimelineRow;
  part: "leader" | "others";
}

/**
 * One stacked segment: 4 px rounded at the top of the stack only, square at the
 * baseline, with a 2 px surface gap between the leader's segment and the rest.
 */
function Segment({
  x = 0,
  y = 0,
  width = 0,
  height = 0,
  fill,
  fillOpacity,
  payload,
  part,
}: SegmentProps) {
  if (height <= 0 || width <= 0 || payload === undefined) return null;
  const onTop = part === "others" || payload.others === 0;
  // The leader sits at the bottom; trim its top so a gap shows under the others.
  const gap = part === "leader" && !onTop ? Math.min(GAP, height - 1) : 0;
  const top = y + gap;
  const h = height - gap;
  const r = onTop ? Math.min(RADIUS, width / 2, h) : 0;
  const right = x + width;
  const bottom = top + h;
  const d =
    r > 0
      ? `M${x},${bottom} V${top + r} Q${x},${top} ${x + r},${top} H${right - r} Q${right},${top} ${right},${top + r} V${bottom} Z`
      : `M${x},${bottom} V${top} H${right} V${bottom} Z`;
  return <path d={d} fill={fill} fillOpacity={fillOpacity} />;
}

interface AxisTickProps {
  x?: number | string;
  y?: number | string;
  payload?: { value?: unknown; offset?: number };
}

const PLOT_HEIGHT = CHART_HEIGHT - MARGIN.top - MARGIN.bottom - X_AXIS_HEIGHT;

/** Quarter labels; January reads as the year, with a rule marking the boundary. */
function YearTick({
  x,
  y,
  payload,
  ticks,
}: AxisTickProps & { ticks: ReturnType<typeof axisTicks> }) {
  const tick = ticks.find((t) => t.start === payload?.value);
  if (tick === undefined) return null;
  const cx = Number(x);
  const cy = Number(y);
  // The boundary sits at the left edge of January's band, between bars.
  const edge = cx - (payload?.offset ?? 0);
  return (
    <g>
      {tick.year ? (
        <line
          x1={edge}
          x2={edge}
          y1={cy - 1}
          y2={cy - 1 - PLOT_HEIGHT}
          stroke="var(--muted)"
          strokeOpacity={0.5}
          strokeDasharray="0"
        />
      ) : null}
      <text
        x={tick.year ? edge + 4 : cx}
        y={cy + 12}
        textAnchor={tick.year ? "start" : "middle"}
        fill={tick.year ? "var(--text)" : "var(--muted)"}
        fontSize={12}
        fontFamily={tick.year ? "var(--ff-num)" : "var(--ff-ui)"}
        fontWeight={tick.year ? 500 : 400}
      >
        {tick.label}
      </text>
    </g>
  );
}

function TimelineTooltip({
  active,
  payload,
  leaderName,
  othersLabel,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: unknown }>;
  leaderName: string | null;
  othersLabel: string;
}) {
  const row = payload?.[0]?.payload as TimelineRow | undefined;
  if (!active || row === undefined) return null;
  return (
    <div className="min-w-48 border border-rule bg-panel px-3 py-2 text-12 shadow-popover">
      <p className="num text-text">{formatBucketRange(row.start, row.end)}</p>
      <dl className="mt-1.5 grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5">
        <dt className="text-text">New creatives</dt>
        <dd className="num text-right text-text">{formatInt(row.total)}</dd>
        {leaderName !== null ? (
          <>
            <dt className="flex items-center gap-1.5 pl-1 text-muted">
              <span className="size-2 rounded-[1px] bg-signal" aria-hidden="true" />
              {leaderName}
            </dt>
            <dd className="num text-right text-muted">{formatInt(row.leader)}</dd>
            <dt className="flex items-center gap-1.5 pl-1 text-muted">
              <span
                className="size-2 rounded-[1px] bg-muted"
                style={{ opacity: OTHERS_OPACITY }}
                aria-hidden="true"
              />
              {othersLabel}
            </dt>
            <dd className="num text-right text-muted">{formatInt(row.others)}</dd>
          </>
        ) : null}
        <dt className="text-text">Ads</dt>
        <dd className="num text-right text-text">{formatInt(row.ads)}</dd>
      </dl>
    </div>
  );
}

/** The chart's numbers for screen readers. */
function TimelineTable({
  caption,
  rows,
  leaderName,
  othersLabel,
}: {
  caption: string;
  rows: TimelineRow[];
  leaderName: string | null;
  othersLabel: string;
}) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Dates</th>
          <th scope="col">New creatives</th>
          {leaderName !== null ? <th scope="col">{leaderName}</th> : null}
          {leaderName !== null ? <th scope="col">{othersLabel}</th> : null}
          <th scope="col">Ads</th>
        </tr>
      </thead>
      <tbody>
        {rows
          .filter((row) => row.total > 0 || row.ads > 0)
          .map((row) => (
            <tr key={row.start}>
              <th scope="row">{formatBucketRange(row.start, row.end)}</th>
              <td>{row.total}</td>
              {leaderName !== null ? <td>{row.leader}</td> : null}
              {leaderName !== null ? <td>{row.others}</td> : null}
              <td>{row.ads}</td>
            </tr>
          ))}
      </tbody>
    </table>
  );
}
