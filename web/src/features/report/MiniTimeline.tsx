import type { Schemas } from "@/api/client";
import { formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

import { hasLaunches, toSeries, type TimelineRow } from "./timeline";

type TimelineBucket = Schemas["TimelineBucketView"];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** `2026-08-01` → `Aug 2026`. */
function monthLabel(iso: string): string {
  const [year, month] = iso.split("-");
  return `${MONTHS[Number(month) - 1] ?? ""} ${year ?? ""}`;
}

interface MiniTimelineProps {
  buckets: TimelineBucket[];
  /** The tallest bar's height, shared so side-by-side charts read on one scale. */
  max?: number;
  /** Only the latest `months` buckets; all of them when left out. */
  months?: number;
  /** Bar area height in px. */
  height?: number;
  className?: string;
}

/**
 * New creatives per month as plain, static bars: the leader's launches at the
 * base in --signal, everyone else's above in --muted. No chart library, no
 * measuring and no motion, so it renders the same in a column and on paper.
 */
export function MiniTimeline({ buckets, max, months, height = 64, className }: MiniTimelineProps) {
  const shown = months === undefined ? buckets : buckets.slice(-months);
  const rows = toSeries(shown, "month");

  if (!hasLaunches(shown)) {
    return <p className={cn("text-12 text-muted", className)}>No launch dates yet.</p>;
  }

  const top = Math.max(max ?? 0, ...rows.map((row) => row.total), 1);
  const first = rows[0];
  const last = rows[rows.length - 1];
  const peak = rows.reduce<TimelineRow | undefined>(
    (best, row) => (best === undefined || row.total > best.total ? row : best),
    undefined,
  );
  const total = rows.reduce((sum, row) => sum + row.total, 0);
  const summary =
    first && last && peak
      ? `${formatInt(total)} new creatives from ${monthLabel(first.start)} to ${monthLabel(last.start)}, peaking at ${formatInt(peak.total)} in ${monthLabel(peak.start)}.`
      : "";

  return (
    <figure className={cn("min-w-0", className)}>
      <div
        role="img"
        aria-label={summary}
        className="flex items-end gap-px border-b border-rule"
        style={{ height }}
      >
        {rows.map((row) => (
          <div
            key={row.start}
            data-testid="mini-bar"
            className="flex h-full min-w-0 flex-1 flex-col justify-end"
          >
            {row.others > 0 ? (
              <span
                className="block w-full bg-muted/75"
                style={{ height: `${(row.others / top) * 100}%` }}
              />
            ) : null}
            {row.leader > 0 ? (
              <span
                className={cn("block w-full bg-signal", row.others > 0 && "mt-px")}
                style={{ height: `${(row.leader / top) * 100}%` }}
              />
            ) : null}
          </div>
        ))}
      </div>
      {first && last ? (
        <figcaption
          className="mt-1 flex justify-between gap-2 text-12 text-muted"
          aria-hidden="true"
        >
          <span className="num">{monthLabel(first.start)}</span>
          {last !== first ? <span className="num">{monthLabel(last.start)}</span> : null}
        </figcaption>
      ) : null}
    </figure>
  );
}
