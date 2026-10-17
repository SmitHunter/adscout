import { describe, expect, it } from "vitest";

import { makeBucket } from "@/features/report/fixtures";

import { factorRows, sharedTimelineMax, sharedTimelines } from "./compare";
import { makeColumn, makeFactors, makeSnapshot } from "./fixtures";

function withVolume(id: number, points: number, value: number | null) {
  return makeColumn({
    snapshot: makeSnapshot({ id }),
    leader_factors: makeFactors().map((f) => (f.key === "volume" ? { ...f, points, value } : f)),
  });
}

describe("factorRows", () => {
  it("cells tied on points with no raw value share best", () => {
    const columns = [withVolume(1, 20, null), withVolume(2, 20, null), withVolume(3, 5, 3)];
    const volume = factorRows(columns, "leader").find((r) => r.key === "volume");
    expect(volume?.best).toEqual([true, true, false]);
  });

  it("a known raw value beats a missing one on equal points", () => {
    const columns = [withVolume(1, 20, null), withVolume(2, 20, 4)];
    const volume = factorRows(columns, "leader").find((r) => r.key === "volume");
    expect(volume?.best).toEqual([false, true]);
  });

  it("every column tied with no raw value: nothing stands out", () => {
    const columns = [withVolume(1, 20, null), withVolume(2, 20, null)];
    const volume = factorRows(columns, "leader").find((r) => r.key === "volume");
    expect(volume?.best).toEqual([false, false]);
  });
});

describe("sharedTimelines", () => {
  it("puts every column on the same months, filling the gaps", () => {
    const older = makeColumn({
      timeline_month: [
        makeBucket({ start: "2025-11-01", new_creatives: 3 }),
        makeBucket({ start: "2026-01-01", new_creatives: 9 }),
      ],
    });
    const newer = makeColumn({
      timeline_month: [makeBucket({ start: "2026-02-01", new_creatives: 2 })],
    });
    const [a, b] = sharedTimelines([older, newer], 4);
    const months = ["2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01"];
    expect(a?.map((m) => m.start)).toEqual(months);
    expect(b?.map((m) => m.start)).toEqual(months);
    expect(a?.map((m) => m.new_creatives)).toEqual([3, 0, 9, 0]);
    expect(b?.map((m) => m.new_creatives)).toEqual([0, 0, 0, 2]);
    expect(sharedTimelineMax([a ?? [], b ?? []])).toBe(9);
  });

  it("crosses a year boundary and keeps only the latest months", () => {
    const column = makeColumn({ timeline_month: [makeBucket({ start: "2026-01-01" })] });
    const [months] = sharedTimelines([column], 3);
    expect(months?.map((m) => m.start)).toEqual(["2025-11-01", "2025-12-01", "2026-01-01"]);
  });

  it("leaves columns without launches empty", () => {
    expect(sharedTimelines([makeColumn({ timeline_month: [] })], 6)).toEqual([[]]);
  });
});
