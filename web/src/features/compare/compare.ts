import type { CompareColumn, Schemas } from "@/api/client";

type FactorResult = Schemas["FactorResult"];
export type FactorKey = FactorResult["key"];
export type FactorView = "leader" | "market";

export const MAX_COMPARE = 4;

/** `?ids=3,1,x,3` → `[3, 1]`: positive whole ids, each once, at most four, in order. */
export function parseIds(raw: string | null): number[] {
  if (!raw) return [];
  const ids: number[] = [];
  for (const part of raw.split(",")) {
    const text = part.trim();
    if (!/^\d+$/.test(text)) continue;
    const id = Number(text);
    if (id > 0 && Number.isSafeInteger(id) && !ids.includes(id)) ids.push(id);
    if (ids.length === MAX_COMPARE) break;
  }
  return ids;
}

/** The factors a column shows: its leader's (as the report does), else the market's. */
export function columnFactors(column: CompareColumn, view: FactorView): FactorResult[] {
  if (view === "leader" && column.leader_factors.length > 0) return column.leader_factors;
  return column.market_factors;
}

export interface FactorRow {
  key: FactorKey;
  /** One per column, in column order; undefined where a column lacks the factor. */
  cells: (FactorResult | undefined)[];
  /** Per column: holds the row's best value. */
  best: boolean[];
}

/** A scored cell's rank: its points, then its raw value to break a tie. */
type Rank = [points: number, value: number];

function rank(factor: FactorResult | undefined): Rank | null {
  if (factor === undefined || factor.status === "pending" || factor.points === null) return null;
  return [factor.points, factor.value ?? Number.NEGATIVE_INFINITY];
}

/** Points first, then raw value. Explicit comparisons, so two `-Infinity` values tie (subtracting them is NaN). */
function compareRanks(a: Rank, b: Rank): number {
  if (a[0] !== b[0]) return a[0] > b[0] ? 1 : -1;
  if (a[1] !== b[1]) return a[1] > b[1] ? 1 : -1;
  return 0;
}

/**
 * Factor rows aligned across columns: each factor once, in the order first
 * seen. The best cell earns the most points for the factor (what the Winner
 * Score counts); the raw value only breaks a tie on points, since a bigger
 * value can score less (longevity's points come from the share of creatives
 * past 90 days, not the oldest ad). Pending cells never win, and when every
 * scored column ties on both, nothing stands out.
 */
export function factorRows(columns: CompareColumn[], view: FactorView): FactorRow[] {
  const perColumn = columns.map((column) => columnFactors(column, view));
  const keys: FactorKey[] = [];
  for (const factors of perColumn) {
    for (const factor of factors) if (!keys.includes(factor.key)) keys.push(factor.key);
  }
  return keys.map((key) => {
    const cells = perColumn.map((factors) => factors.find((f) => f.key === key));
    const ranks = cells.map(rank);
    const scored = ranks.filter((r): r is Rank => r !== null);
    const top = scored.reduce<Rank | null>(
      (best, r) => (best === null || compareRanks(r, best) > 0 ? r : best),
      null,
    );
    const allTie =
      scored.length > 1 && top !== null && scored.every((r) => compareRanks(r, top) === 0);
    const best = ranks.map(
      (r) =>
        r !== null && top !== null && compareRanks(r, top) === 0 && !allTie && columns.length > 1,
    );
    return { key, cells, best };
  });
}

type TimelineBucket = CompareColumn["timeline_month"][number];

/** `2026-03-01` shifted by `delta` months, as `YYYY-MM-01`. */
function shiftMonth(start: string, delta: number): string {
  const [year = 0, month = 1] = start.split("-").map(Number);
  const index = year * 12 + (month - 1) + delta;
  const y = Math.floor(index / 12);
  const m = (index % 12) + 1;
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-01`;
}

/**
 * Each column's monthly buckets over one shared window: the latest `months`
 * months up to the newest month any column has, with empty months filled in.
 * Side by side, a bar then sits under the same month in every column.
 */
export function sharedTimelines(columns: CompareColumn[], months: number): TimelineBucket[][] {
  const ends = columns.flatMap((c) => c.timeline_month.map((b) => b.start.slice(0, 10)));
  const end = ends.reduce<string | null>((a, b) => (a === null || b > a ? b : a), null);
  if (end === null) return columns.map(() => []);
  const window = Array.from({ length: months }, (_, i) => shiftMonth(end, i - months + 1));
  return columns.map((column) => {
    const byMonth = new Map(column.timeline_month.map((b) => [b.start.slice(0, 10), b]));
    return window.map(
      (start) => byMonth.get(start) ?? { start, new_creatives: 0, ads: 0, leader_new_creatives: 0 },
    );
  });
}

/** The tallest month across the timelines, so they share one scale. */
export function sharedTimelineMax(timelines: TimelineBucket[][]): number {
  return Math.max(1, ...timelines.flatMap((t) => t.map((b) => b.new_creatives)));
}
