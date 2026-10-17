import { describe, expect, it } from "vitest";

import { isScored } from "./analysis";
import { makeAnalysis } from "./fixtures";

const base = makeAnalysis().angles[0]!;

describe("isScored", () => {
  it("counts creatives, not ads", () => {
    expect(isScored({ ...base, creatives: 1, ads: 3 })).toBe(false);
    expect(isScored({ ...base, creatives: 2, ads: 2 })).toBe(true);
  });
});
