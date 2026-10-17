import type { AnalysisView, Report, Schemas } from "@/api/client";

type AngleView = Schemas["AnalysisAngleView"];
type Emotion = AngleView["emotion"];
type OfferKind = Schemas["Offer"]["kind"];
type Seasonality = Schemas["AngleAnalysis"]["seasonality"];
type AdView = Schemas["AdView"];

/** Creatives sent per analysis (one lead ad each); the backend caps at the same number. */
export const MAX_CREATIVES = 150;

export const UNAVAILABLE_COPY =
  "Angle analysis needs Claude. Install Claude Code or set ANTHROPIC_API_KEY in .env, then restart AdScout.";

export const NO_RELEVANT_COPY = "No relevant ads to analyze.";

export const EMOTION_LABEL: Record<Emotion, string> = {
  pain_relief: "Pain relief",
  confidence: "Confidence",
  appearance: "Appearance",
  convenience: "Convenience",
  health: "Health",
  gift: "Gift",
  fear_of_missing_out: "Fear of missing out",
  social_proof: "Social proof",
  value: "Value",
  other: "Other",
};

export const OFFER_LABEL: Record<OfferKind, string> = {
  discount: "Discount",
  bundle: "Bundle",
  free_shipping: "Free shipping",
  guarantee: "Guarantee",
  free_gift: "Free gift",
  urgency: "Urgency",
  other: "Other",
};

export const SEASONALITY: Record<Seasonality, { label: string; className: string }> = {
  evergreen: { label: "Evergreen", className: "border-pass/50 text-pass" },
  seasonal: { label: "Seasonal", className: "border-warn/50 text-warn" },
  unclear: { label: "Unclear", className: "border-muted/60 text-muted" },
};

export const BACKEND_LABEL: Record<string, string> = {
  cli: "Claude Code",
  api: "Anthropic API",
  demo: "Sample analysis",
};

export function backendLabel(backend: string | null | undefined): string {
  return backend ? (BACKEND_LABEL[backend] ?? backend) : "Claude";
}

/** `claude-sonnet-5-5` → `Claude Sonnet 5.5`; anything else as sent. */
export function modelLabel(model: string | null | undefined): string {
  if (!model) return "Claude";
  const match = /^claude-([a-z]+)-(\d+)-(\d+)$/.exec(model);
  if (match === null) return model;
  const [, family = "", major, minor] = match;
  return `Claude ${family.charAt(0).toUpperCase()}${family.slice(1)} ${major}.${minor}`;
}

/** The creatives one run sends: the relevant ones, capped. */
export function creativesToSend(report: Report): number {
  return Math.min(report.score.creatives, MAX_CREATIVES);
}

export function confirmCopy(n: number, backend: string | null | undefined): string {
  return `This sends ${n} creatives to Claude (${backendLabel(backend)}) and uses your quota. Continue?`;
}

/** Live counts first: most creatives, then most ads, then the order Claude gave. */
export function sortAngles(angles: AngleView[]): AngleView[] {
  return angles
    .map((angle, index) => ({ angle, index }))
    .sort(
      (a, b) =>
        b.angle.creatives - a.angle.creatives || b.angle.ads - a.angle.ads || a.index - b.index,
    )
    .map(({ angle }) => angle);
}

/** `33.33%` of the widest angle; at least a sliver for any angle with creatives. */
export function barWidth(creatives: number, max: number): string {
  if (max <= 0 || creatives <= 0) return "0%";
  return `${Math.round((creatives / max) * 10000) / 100}%`;
}

/** The market rule counts an angle once at least two relevant creatives carry it. */
export function isScored(angle: AngleView): boolean {
  return angle.creatives >= 2;
}

/** Distinct creatives among the ads excluded because Claude marked them off-topic. */
export function offTopicCreatives(ads: AdView[]): number {
  return new Set(ads.filter((ad) => ad.reason === "claude_off_topic").map((ad) => ad.creative_key))
    .size;
}

/** Up to `limit` distinct example copies from the angle's ads, in the order Claude listed them. */
export function exampleCopies(
  ids: string[],
  ads: AdView[],
  limit = 2,
): { id: string; body: string; advertiser: string }[] {
  const byId = new Map(ads.map((ad) => [ad.ad_archive_id, ad]));
  const seen = new Set<string>();
  const out: { id: string; body: string; advertiser: string }[] = [];
  for (const id of ids) {
    const ad = byId.get(id);
    // Line breaks fold into spaces: the example is clamped to three lines.
    const body = ad?.body?.replace(/\s+/g, " ").trim();
    if (ad === undefined || !body || seen.has(body)) continue;
    seen.add(body);
    out.push({ id, body, advertiser: ad.page_name.trim() || ad.page_id });
    if (out.length === limit) break;
  }
  return out;
}

/** Offers grouped by kind, kinds in the order they first appear. */
export function groupOffers(
  offers: AnalysisView["result"]["offers"],
): { kind: OfferKind; texts: string[] }[] {
  const groups = new Map<OfferKind, string[]>();
  for (const offer of offers) {
    const texts = groups.get(offer.kind);
    if (texts) {
      if (!texts.includes(offer.text)) texts.push(offer.text);
    } else groups.set(offer.kind, [offer.text]);
  }
  return Array.from(groups, ([kind, texts]) => ({ kind, texts }));
}
