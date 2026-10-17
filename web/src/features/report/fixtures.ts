import type { Report, Schemas } from "@/api/client";

export type FactorResult = Schemas["FactorResult"];
export type TimelineBucket = Schemas["TimelineBucketView"];

/** Test and preview data shaped like GET /api/snapshots/{id}/report. */
export function makeFactor(overrides: Partial<FactorResult> = {}): FactorResult {
  return {
    key: "longevity",
    label: "Longevity",
    value: 120,
    display: "120 days",
    threshold: { fail: 30, pass: 90, full: 180 },
    status: "pass",
    score: 0.8,
    points: 24,
    max_points: 30,
    detail: "40% of creatives running ≥ 90 days",
    ...overrides,
  };
}

export function makeBucket(overrides: Partial<TimelineBucket> = {}): TimelineBucket {
  return { start: "2026-08-01", new_creatives: 0, ads: 0, leader_new_creatives: 0, ...overrides };
}

export function makeReport(overrides: Partial<Report> = {}): Report {
  const factors: FactorResult[] = [
    makeFactor(),
    makeFactor({
      key: "continuity",
      label: "Continuity",
      value: 6,
      display: "6/6 months",
      threshold: { fail: 3, pass: 5, full: null },
      points: 20,
      max_points: 20,
      detail: "Months of the last 6 with at least one new creative",
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
  return {
    snapshot: {
      id: 1,
      query: "posture corrector",
      kind: "keyword",
      page_id: null,
      country: "US",
      active_status: "active",
      media_type: "all",
      status: "ok",
      started_at: "2026-10-17T04:01:33Z",
      finished_at: "2026-10-17T04:02:12Z",
      ad_count: 100,
      advertiser_count: 39,
      error_message: null,
    },
    score: {
      total: 98,
      headline: 91,
      provisional: true,
      factors,
      leader: {
        advertiser_key: "514393445092458",
        page_name: "Northbrace",
        ads: 30,
        creatives: 27,
        score: 91,
        factors: factors.map((f) =>
          f.key === "longevity" ? { ...f, value: 260, display: "260 days" } : f,
        ),
      },
      advertisers: 26,
      saturation: "crowded",
      relevant_ads: 75,
      excluded_ads: 25,
      creatives: 65,
      warnings: [],
      decisions: {},
    },
    timeline_month: [
      makeBucket({ start: "2026-07-01", new_creatives: 4, ads: 12, leader_new_creatives: 2 }),
      makeBucket({ start: "2026-08-01", new_creatives: 20, ads: 31, leader_new_creatives: 16 }),
    ],
    timeline_week: [
      makeBucket({ start: "2026-08-03", new_creatives: 4, ads: 7, leader_new_creatives: 4 }),
    ],
    advertisers: [],
    ads: [],
    notice: null,
    analysis: null,
    ...overrides,
  };
}

export type AdView = Schemas["AdView"];
export type AdvertiserRow = Schemas["AdvertiserRow"];

export function makeAd(overrides: Partial<AdView> = {}): AdView {
  const id = overrides.ad_archive_id ?? "1001";
  return {
    source: "meta_web",
    ad_archive_id: id,
    page_id: "514393445092458",
    advertiser_key: "514393445092458",
    page_name: "Northbrace",
    start_date: "2026-03-01T08:00:00Z",
    end_date: null,
    is_active: true,
    body: "Fix your posture in 14 days with the Northbrace posture corrector.",
    title: null,
    link_url: "https://northbrace.test/products/posture",
    link_caption: "northbrace.test",
    cta_text: "Shop now",
    display_format: "IMAGE",
    media: [
      {
        type: "image",
        url: `https://cdn.test/${id}.jpg`,
        preview_url: null,
        local_path: `1/${id}_0.jpg`,
        poster_path: null,
        phash: null,
      },
    ],
    collation_id: null,
    collation_count: 1,
    platforms: ["facebook", "instagram"],
    relevant: true,
    reason: "keyword",
    creative_key: `c-${id}`,
    days_running: 230,
    landing_domain: "northbrace.test",
    ...overrides,
  };
}

export function makeAdvertiser(overrides: Partial<AdvertiserRow> = {}): AdvertiserRow {
  return {
    key: "514393445092458",
    name: "Northbrace",
    ads: 30,
    total_ads: 30,
    creatives: 27,
    oldest_start: "2026-03-01T08:00:00Z",
    days_running_max: 230,
    landing_domain: "northbrace.test",
    excluded: false,
    is_leader: true,
    ...overrides,
  };
}

export type AnalysisView = Schemas["AnalysisView"];
export type AnalysisJob = Schemas["AnalysisJob"];
export type Settings = Schemas["SettingsView"];

/** A stored analysis as the report carries it, with live per-angle counts. */
export function makeAnalysis(overrides: Partial<AnalysisView> = {}): AnalysisView {
  const angles: AnalysisView["angles"] = [
    {
      name: "Desk-job back pain",
      description: "Sells relief for office workers whose backs ache after long days at a desk.",
      emotion: "pain_relief",
      ad_ids: ["1001", "1002", "1003"],
      creatives: 3,
      ads: 5,
    },
    {
      name: "Look taller and confident",
      description: "Straighter posture as an instant confidence and appearance upgrade.",
      emotion: "confidence",
      ad_ids: ["1004", "1005"],
      creatives: 9,
      ads: 12,
    },
    {
      name: "Gift for parents",
      description: "Positions the corrector as a caring gift for older relatives.",
      emotion: "gift",
      ad_ids: ["1006"],
      creatives: 1,
      ads: 1,
    },
  ];
  return {
    result: {
      angles: angles.map(({ name, description, emotion, ad_ids }) => ({
        name,
        description,
        emotion,
        ad_ids,
      })),
      hooks: [
        { text: "Your back shouldn't hurt at 30.", ad_ids: ["1001"] },
        { text: "Doctors hate how simple this is.", ad_ids: ["1004"] },
      ],
      offers: [
        { kind: "discount", text: "50% off today", ad_ids: ["1001"] },
        { kind: "discount", text: "Buy 2 get 1 free", ad_ids: ["1002"] },
        { kind: "free_shipping", text: "Free shipping worldwide", ad_ids: ["1004"] },
      ],
      not_product_ad_ids: ["2001"],
      seasonality: "evergreen",
      seasonality_note: "Launches spread evenly across the year with no holiday spikes.",
      verdict:
        "Northbrace dominates with pain-relief copy that has run for months. The confidence angle is under-used by smaller sellers. A fresh appearance-led creative could still find room.",
    },
    angles,
    model: "claude-sonnet-5-5",
    backend: "cli",
    created_at: "2026-10-17T05:12:00Z",
    ad_count: 64,
    flagged_off_topic: 0,
    ...overrides,
  };
}

export function makeJob(overrides: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    snapshot_id: 1,
    state: "running",
    message: null,
    started_at: "2026-10-17T05:11:00Z",
    finished_at: null,
    model: "claude-sonnet-5-5",
    backend: "cli",
    creatives: 64,
    ...overrides,
  };
}

export function makeSettings(overrides: Partial<Settings> = {}): Settings {
  return {
    timezone: "Australia/Melbourne",
    max_results: 300,
    has_meta_token: false,
    version: "0.4.0",
    analysis: { available: true, backend: "cli", reason: null },
    analysis_backend: "auto",
    analysis_model: "claude-sonnet-5-5",
    auto_analyze: false,
    scroll_delay_min: 2,
    scroll_delay_max: 5,
    daily_check_time: "09:00",
    desktop_notifications: false,
    notifications_available: false,
    max_auto_analyses_per_day: 5,
    demo: false,
    ...overrides,
  };
}
