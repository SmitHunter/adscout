import type { SearchQuery, Snapshot, TrendPoint, WatchItem } from "@/api/client";
import { formatDayMonth, formatTime } from "@/lib/format";

/** The search a watch item re-runs, as identity: kind, keyword or page, country, filters. */
export interface WatchTarget {
  kind: "keyword" | "advertiser";
  query: string;
  page_id: string | null;
  country: string;
  active_status: string;
  media_type: string;
}

/** The watch item that re-runs `target`, matched the way the API does. */
export function findWatchItem(
  items: readonly WatchItem[] | undefined,
  target: WatchTarget,
): WatchItem | undefined {
  return items?.find(
    (item) =>
      item.kind === target.kind &&
      item.country === target.country &&
      item.active_status === target.active_status &&
      item.media_type === target.media_type &&
      (target.kind === "advertiser"
        ? item.page_id === target.page_id
        : item.query === target.query),
  );
}

export function targetOfSnapshot(snapshot: Snapshot): WatchTarget {
  return {
    kind: snapshot.kind,
    query: snapshot.query,
    page_id: snapshot.page_id,
    country: snapshot.country,
    active_status: snapshot.active_status === "all" ? "all" : "active",
    media_type: isMediaType(snapshot.media_type) ? snapshot.media_type : "all",
  };
}

/** An advertiser page view: every country unless the report was for one market. */
export function advertiserTarget(pageId: string, name: string, country: string): WatchTarget {
  return {
    kind: "advertiser",
    query: name,
    page_id: pageId,
    // The page view takes a country code or ALL; EU goes through the API, not here.
    country: country === "EU" || country === "" ? "ALL" : country,
    active_status: "active",
    media_type: "all",
  };
}

/** The POST /api/watch body (the API applies the search-limit setting at run time). */
export function watchQuery(target: WatchTarget, limit: number): SearchQuery {
  return {
    kind: target.kind,
    keyword: target.query,
    page_id: target.kind === "advertiser" ? target.page_id : null,
    country: target.country,
    active_status: target.active_status === "all" ? "all" : "active",
    media_type: isMediaType(target.media_type) ? target.media_type : "all",
    limit: Math.min(Math.max(limit, 1), 1000),
  };
}

export function isMediaType(value: string): value is SearchQuery["media_type"] {
  return value === "all" || value === "image" || value === "video" || value === "meme";
}

/** A numeric Ad Library page id: only those open as an advertiser page view. */
export function isPageId(key: string): boolean {
  return /^\d+$/.test(key);
}

export const KIND_LABEL: Record<WatchItem["kind"], string> = {
  keyword: "Product",
  advertiser: "Advertiser",
};

/** Where an item is watched: an advertiser can be watched in every country (`ALL`). */
export function scopeLabel(country: string): string {
  return country === "ALL" ? "All countries" : country;
}

export type Tone = "muted" | "signal" | "warn" | "fail" | "text";

/** The last check's outcome, as the watchlist and item page show it. */
export function checkStatus(item: WatchItem): { text: string; tone: Tone } {
  if (item.running) {
    return { text: downloadingMedia(item) ? "Downloading media" : "Checking", tone: "signal" };
  }
  switch (item.last_status) {
    case null:
      return { text: "Not checked yet", tone: "muted" };
    case "ok":
    case "done":
      return { text: "OK", tone: "text" };
    case "capped":
      return { text: "Capped", tone: "muted" };
    case "blocked":
      return { text: "Blocked", tone: "warn" };
    case "error":
      return { text: "Failed", tone: "fail" };
    case "cancelled":
      return { text: "Cancelled", tone: "muted" };
    default:
      return { text: item.last_status, tone: "muted" };
  }
}

const FINISHED = new Set(["ok", "done", "capped", "blocked", "error", "cancelled"]);

/**
 * The check's search has finished but the run hasn't: it is saving the ads'
 * images and videos, which can take minutes on a big search. The latest
 * snapshot is this run's when it started after the item's last run.
 */
function downloadingMedia(item: WatchItem): boolean {
  const latest = item.latest;
  if (latest === null || !FINISHED.has(latest.status)) return false;
  const since = new Date(item.last_run_at ?? item.created_at).getTime();
  return new Date(latest.started_at).getTime() > since;
}

export const TONE_TEXT: Record<Tone, string> = {
  muted: "text-muted",
  signal: "text-signal",
  warn: "text-warn",
  fail: "text-fail",
  text: "text-text",
};

/** The soonest scheduled check across enabled items, or null when none is scheduled. */
export function nextRun(items: readonly WatchItem[]): string | null {
  const times = items
    .filter((i) => i.enabled && i.next_run_at !== null)
    .map((i) => i.next_run_at as string)
    .sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
  return times[0] ?? null;
}

/**
 * `17 Oct`, or `17 Oct 15:01` when another check ran the same day (a Run now
 * beside the daily check), so two checks never read as one date.
 */
export function checkLabel(
  points: readonly TrendPoint[],
  index: number,
  timeZone?: string,
): string {
  const point = points[index];
  if (point === undefined) return "";
  const day = formatDayMonth(point.date);
  const sameDay = points.some((p, i) => i !== index && p.date === point.date);
  return sameDay ? `${day} ${formatTime(point.started_at, timeZone)}` : day;
}
