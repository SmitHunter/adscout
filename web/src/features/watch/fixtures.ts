import type { TrendPoint, WatchAlert, WatchItem } from "@/api/client";

export function makeWatchItem(overrides: Partial<WatchItem> = {}): WatchItem {
  return {
    id: 1,
    kind: "keyword",
    query: "posture corrector",
    page_id: null,
    country: "US",
    active_status: "active",
    media_type: "all",
    enabled: true,
    created_at: "2026-10-03T22:00:00Z",
    last_run_at: "2026-10-16T22:02:00Z",
    last_status: "ok",
    latest: null,
    latest_point: {
      snapshot_id: 14,
      date: "2026-10-17",
      started_at: "2026-10-16T22:01:00Z",
      active_ads: 182,
      capped: false,
      advertisers: 41,
      winner_score: 78,
      market_score: 84,
    },
    sparkline: [96, 101, 99, 110, 118, 116, 125, 131, 140, 138, 152, 160, 171, 182],
    next_run_at: "2026-10-17T22:00:00Z",
    unread_alerts: 0,
    running: false,
    ...overrides,
  };
}

export function makePoint(overrides: Partial<TrendPoint> = {}): TrendPoint {
  return {
    snapshot_id: 1,
    date: "2026-10-04",
    started_at: "2026-10-03T22:01:00Z",
    active_ads: 96,
    capped: false,
    new_creatives: 96,
    advertisers: 30,
    winner_score: 61,
    market_score: 70,
    ...overrides,
  };
}

/** `n` daily checks from 4 Oct, active ads rising with a capped run in the middle. */
export function makePoints(n = 6): TrendPoint[] {
  return Array.from({ length: n }, (_, i) => {
    const day = 4 + i;
    return makePoint({
      snapshot_id: i + 1,
      date: `2026-10-${String(day).padStart(2, "0")}`,
      started_at: `2026-10-${String(day - 1).padStart(2, "0")}T22:01:00Z`,
      active_ads: 96 + i * 20,
      capped: i === 2,
      new_creatives: i === 0 ? 96 : 4 + i,
      advertisers: 30 + i,
      winner_score: 61 + i * 3,
    });
  });
}

export function makeAlert(overrides: Partial<WatchAlert> = {}): WatchAlert {
  return {
    id: 1,
    watch_item_id: 1,
    snapshot_id: 14,
    query: "posture corrector",
    kind: "change",
    change_pct: 38,
    message: "posture corrector: active ads up 38% (132 → 182)",
    created_at: "2026-10-16T22:02:00Z",
    read: false,
    ...overrides,
  };
}
