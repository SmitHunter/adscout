import type { CompareColumn, Snapshot } from "@/api/client";
import { makeBucket, makeFactor, makeReport, type FactorResult } from "@/features/report/fixtures";

export function makeSnapshot(overrides: Partial<Snapshot> = {}): Snapshot {
  return { ...makeReport().snapshot, ...overrides };
}

/** Longevity, continuity, volume and angles, with `values` swapped in by key. */
export function makeFactors(
  values: Partial<Record<FactorResult["key"], number | null>> = {},
): FactorResult[] {
  const base: FactorResult[] = [
    makeFactor(),
    makeFactor({
      key: "continuity",
      label: "Continuity",
      value: 5,
      display: "5/6 months",
      threshold: { fail: 3, pass: 5, full: null },
      points: 20,
      max_points: 20,
      detail: "Months of the last 6 with at least one new creative",
    }),
    makeFactor({
      key: "volume",
      label: "Creative volume",
      value: 12,
      display: "12 creatives",
      threshold: { fail: 3, pass: 8, full: 20 },
      points: 15,
      max_points: 25,
      detail: "Distinct creatives from the leader",
    }),
    makeFactor({
      key: "angles",
      label: "Angle diversity",
      value: null,
      display: "—",
      threshold: { fail: 2, pass: 3, full: 5 },
      status: "pending",
      score: null,
      points: null,
      max_points: 25,
      detail: "Needs angle analysis",
    }),
  ];
  return base.map((factor) => {
    const value = values[factor.key];
    if (value === undefined) return factor;
    return value === null
      ? { ...factor, value: null, display: "—", status: "pending", points: null }
      : { ...factor, value, display: String(value), status: "pass" };
  });
}

export function makeColumn(overrides: Partial<CompareColumn> = {}): CompareColumn {
  const factors = makeFactors();
  return {
    snapshot: makeSnapshot(),
    score: 91,
    market_score: 98,
    provisional: true,
    relevant_ads: 75,
    advertisers: 26,
    creatives: 65,
    leader: {
      advertiser_key: "514393445092458",
      page_name: "Northbrace",
      ads: 30,
      creatives: 27,
      score: 91,
    },
    leader_factors: factors,
    market_factors: factors,
    timeline_month: [
      makeBucket({ start: "2026-07-01", new_creatives: 4, ads: 12, leader_new_creatives: 2 }),
      makeBucket({ start: "2026-08-01", new_creatives: 20, ads: 31, leader_new_creatives: 16 }),
    ],
    analysis: null,
    ...overrides,
  };
}
