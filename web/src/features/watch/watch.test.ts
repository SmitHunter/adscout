import { describe, expect, it } from "vitest";

import { makeSnapshot } from "@/features/compare/fixtures";

import { makePoint, makeWatchItem } from "./fixtures";
import { checkLabel, checkStatus } from "./watch";

const TZ = "Australia/Melbourne";

describe("checkLabel", () => {
  it("is the date alone when each check has its own day", () => {
    const points = [
      makePoint({ snapshot_id: 1, date: "2026-10-16", started_at: "2026-10-15T22:00:00Z" }),
      makePoint({ snapshot_id: 2, date: "2026-10-17", started_at: "2026-10-16T22:00:00Z" }),
    ];
    expect(checkLabel(points, 0, TZ)).toBe("16 Oct");
    expect(checkLabel(points, 1, TZ)).toBe("17 Oct");
  });

  it("adds the local time when two checks share a day", () => {
    const points = [
      makePoint({ snapshot_id: 1, date: "2026-10-17", started_at: "2026-10-17T04:01:33Z" }),
      makePoint({ snapshot_id: 3, date: "2026-10-17", started_at: "2026-10-17T12:23:33Z" }),
      makePoint({ snapshot_id: 4, date: "2026-10-18", started_at: "2026-10-17T22:00:00Z" }),
    ];
    expect(checkLabel(points, 0, TZ)).toBe("17 Oct 15:01");
    expect(checkLabel(points, 1, TZ)).toBe("17 Oct 23:23");
    expect(checkLabel(points, 2, TZ)).toBe("18 Oct");
    expect(checkLabel(points, 9, TZ)).toBe("");
  });
});

describe("checkStatus", () => {
  const item = (status: string, startedAt: string, lastRun: string | null) =>
    makeWatchItem({
      running: true,
      created_at: "2026-10-17T12:00:00Z",
      last_run_at: lastRun,
      latest: makeSnapshot({ id: 3, status, started_at: startedAt }),
    });

  it("is checking while this run's search is going", () => {
    expect(checkStatus(item("running", "2026-10-17T12:23:00Z", null)).text).toBe("Checking");
  });

  it("is downloading media once this run's search has finished", () => {
    expect(checkStatus(item("capped", "2026-10-17T12:23:00Z", null))).toEqual({
      text: "Downloading media",
      tone: "signal",
    });
  });

  it("an earlier snapshot finishing doesn't count", () => {
    // Linked before the item existed, or the previous day's check.
    expect(checkStatus(item("ok", "2026-10-17T04:00:00Z", null)).text).toBe("Checking");
    expect(checkStatus(item("ok", "2026-10-17T13:00:00Z", "2026-10-17T13:05:00Z")).text).toBe(
      "Checking",
    );
  });
});
