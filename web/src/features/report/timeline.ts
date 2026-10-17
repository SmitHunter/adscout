import type { Schemas } from "@/api/client";

type TimelineBucket = Schemas["TimelineBucketView"];
export type Granularity = "month" | "week";

export interface TimelineRow {
  start: string;
  /** The bucket's last day, inclusive. */
  end: string;
  leader: number;
  others: number;
  total: number;
  ads: number;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY_MS = 86_400_000;

function parseDay(iso: string): Date {
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`);
}

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function bucketEnd(start: string, granularity: Granularity): string {
  const date = parseDay(start);
  if (granularity === "week") return isoDay(new Date(date.getTime() + 6 * DAY_MS));
  return isoDay(new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)));
}

/** Splits each bucket's new creatives into the leader's and everyone else's. */
export function toSeries(buckets: TimelineBucket[], granularity: Granularity): TimelineRow[] {
  return buckets.map((bucket) => {
    const leader = Math.min(bucket.leader_new_creatives, bucket.new_creatives);
    return {
      start: bucket.start,
      end: bucketEnd(bucket.start, granularity),
      leader,
      others: bucket.new_creatives - leader,
      total: bucket.new_creatives,
      ads: bucket.ads,
    };
  });
}

export function hasLaunches(buckets: TimelineBucket[]): boolean {
  return buckets.some((bucket) => bucket.new_creatives > 0);
}

/** `27 Jul – 2 Aug 2026`, `1–31 Aug 2026`, `29 Dec 2025 – 4 Jan 2026`. */
export function formatBucketRange(start: string, end: string): string {
  const a = parseDay(start);
  const b = parseDay(end);
  const day = (d: Date) => d.getUTCDate();
  const month = (d: Date) => MONTHS[d.getUTCMonth()] ?? "";
  const year = (d: Date) => d.getUTCFullYear();
  if (year(a) !== year(b)) {
    return `${day(a)} ${month(a)} ${year(a)} – ${day(b)} ${month(b)} ${year(b)}`;
  }
  if (a.getUTCMonth() !== b.getUTCMonth()) {
    return `${day(a)} ${month(a)} – ${day(b)} ${month(b)} ${year(b)}`;
  }
  return `${day(a)}–${day(b)} ${month(b)} ${year(b)}`;
}

export interface AxisTick {
  start: string;
  label: string;
  /** A January bucket: the axis draws a year boundary before it. */
  year: boolean;
}

/**
 * X-axis ticks at quarter (or half-year) starts. January ticks read as the year and mark a
 * boundary; the first tick carries its year so the oldest bars are dated too.
 */
export function axisTicks(rows: TimelineRow[], everyMonths: 3 | 6 = 3): AxisTick[] {
  const ticks: AxisTick[] = [];
  let previousMonth = -1;
  rows.forEach((row, index) => {
    const date = parseDay(row.start);
    const monthIndex = date.getUTCMonth();
    const newMonth = monthIndex !== previousMonth;
    previousMonth = monthIndex;
    if (index === 0 || !newMonth || monthIndex % everyMonths !== 0) return;
    const year = monthIndex === 0;
    const label = year
      ? String(date.getUTCFullYear())
      : ticks.length === 0
        ? `${MONTHS[monthIndex]} ${date.getUTCFullYear()}`
        : (MONTHS[monthIndex] ?? "");
    ticks.push({ start: row.start, label, year });
  });
  return ticks;
}
