import { describe, expect, it } from "vitest";

const sources = import.meta.glob<string>(["/src/**/*.tsx", "!/src/**/*.test.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
});

describe("design tokens", () => {
  it("dense type uses the font-dense token, never a raw font-stretch", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(20);
    const raw = Object.entries(sources)
      .filter(([, text]) => /font-stretch:\s*\d/.test(text))
      .map(([path]) => path);
    expect(raw).toEqual([]);
  });
});
