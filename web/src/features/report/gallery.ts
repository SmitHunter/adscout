import type { Schemas } from "@/api/client";

export type AdView = Schemas["AdView"];
export type MediaItem = Schemas["MediaItem"];
export type AdvertiserRow = Schemas["AdvertiserRow"];
export type Reason = AdView["reason"];

/** One tile: the ads that share a creative, led by its longest-running ad. */
export interface CreativeGroup {
  key: string;
  lead: AdView;
  ads: AdView[];
  /** Other ads in the group (`+{k} variants`). */
  variants: number;
  /** Ads Meta counts behind this creative, collations included (`{n} ads use this`). */
  adCount: number;
  daysRunning: number;
  /** Latest start date in the group, for `Newest`. */
  newest: string;
}

export type SortKey = "longest" | "newest" | "most";
export type MediaFilter = "all" | "image" | "video";

/** A gallery's sort and filters, kept by `ReportTabs` so they survive tab switches. */
export interface GalleryFilters {
  sort: SortKey;
  /** An advertiser key, or "all". */
  advertiser: string;
  media: MediaFilter;
}

export const DEFAULT_FILTERS: GalleryFilters = { sort: "longest", advertiser: "all", media: "all" };

export const SORT_LABELS: Record<SortKey, string> = {
  longest: "Longest running",
  newest: "Newest",
  most: "Most ads",
};

function byLongest(a: AdView, b: AdView): number {
  return (
    b.days_running - a.days_running ||
    a.start_date.localeCompare(b.start_date) ||
    a.ad_archive_id.localeCompare(b.ad_archive_id)
  );
}

/** Group ads by creative, keeping first-seen order; the lead is the longest-running ad. */
export function groupCreatives(ads: AdView[]): CreativeGroup[] {
  const groups = new Map<string, AdView[]>();
  for (const ad of ads) {
    const members = groups.get(ad.creative_key);
    if (members) members.push(ad);
    else groups.set(ad.creative_key, [ad]);
  }
  return Array.from(groups, ([key, members]) => {
    const sorted = [...members].sort(byLongest);
    const lead = sorted[0] as AdView;
    return {
      key,
      lead,
      ads: sorted,
      variants: sorted.length - 1,
      adCount: sorted.reduce((sum, ad) => sum + Math.max(ad.collation_count, 1), 0),
      daysRunning: lead.days_running,
      newest: sorted.reduce((max, ad) => (ad.start_date > max ? ad.start_date : max), ""),
    };
  });
}

export function sortGroups(groups: CreativeGroup[], sort: SortKey): CreativeGroup[] {
  const tie = (a: CreativeGroup, b: CreativeGroup) => byLongest(a.lead, b.lead);
  const compare: Record<SortKey, (a: CreativeGroup, b: CreativeGroup) => number> = {
    longest: tie,
    newest: (a, b) => b.newest.localeCompare(a.newest) || tie(a, b),
    most: (a, b) => b.adCount - a.adCount || tie(a, b),
  };
  return [...groups].sort(compare[sort]);
}

/** The advertiser key the API uses for overrides, as the backend computed it. */
export function advertiserKey(ad: AdView): string {
  return ad.advertiser_key ?? "";
}

export function advertiserName(ad: AdView): string {
  return ad.page_name.trim() || ad.page_id;
}

export function mediaKind(group: CreativeGroup): "image" | "video" | "none" {
  return group.lead.media[0]?.type ?? "none";
}

function matches(ad: AdView, advertiser: string | null, media: MediaFilter): boolean {
  return (
    (advertiser === null || advertiserKey(ad) === advertiser) &&
    // The tile shows an ad's first media item, so that is the one the filter reads.
    (media === "all" || ad.media[0]?.type === media)
  );
}

/**
 * Creatives with at least one ad matching the filters. The tile is led by the
 * longest-running matching ad, so a tile filtered to an advertiser is labelled
 * with that advertiser and an Images filter never shows a video. The group's
 * ads and counts are unchanged: a toggle still acts on the whole creative.
 */
export function filterGroups(
  groups: CreativeGroup[],
  advertiser: string | null,
  media: MediaFilter,
): CreativeGroup[] {
  if (advertiser === null && media === "all") return groups;
  return groups.flatMap((group) => {
    const lead = group.ads.find((ad) => matches(ad, advertiser, media));
    return lead === undefined ? [] : [{ ...group, lead, daysRunning: lead.days_running }];
  });
}

/** `1/123_0.jpg` → `/media/1/123_0.jpg`, each segment URL-encoded. */
export function localMediaUrl(relative: string): string {
  return `/media/${relative.split("/").map(encodeURIComponent).join("/")}`;
}

/**
 * Image sources for a tile, best first: the saved file (image file or video
 * poster), then the CDN (image url or video preview), which may have expired.
 */
export function imageSources(item: MediaItem | undefined): string[] {
  if (item === undefined) return [];
  const local = item.type === "video" ? item.poster_path : item.local_path;
  const remote = item.type === "video" ? item.preview_url : item.url;
  return [local ? localMediaUrl(local) : null, remote || null].filter(
    (src): src is string => src !== null,
  );
}

/** The saved video file, which is the only thing that plays inline. */
export function localVideo(item: MediaItem | undefined): string | null {
  return item?.type === "video" && item.local_path ? localMediaUrl(item.local_path) : null;
}

export const COPY_PREVIEW = 140;

/**
 * The first 140 characters of the copy, line breaks folded into spaces and cut
 * at a word boundary when one is close; the full text keeps its line breaks.
 */
export function copyPreview(text: string): { short: string; truncated: boolean } {
  const flat = text.replace(/\s+/g, " ").trim();
  if (flat.length <= COPY_PREVIEW) return { short: flat, truncated: false };
  const cut = flat.slice(0, COPY_PREVIEW);
  const space = cut.lastIndexOf(" ");
  const short = space >= COPY_PREVIEW - 24 ? cut.slice(0, space) : cut;
  return { short: `${short.replace(/[\s.,;:!?-]+$/, "")}…`, truncated: true };
}

export const REASON_COPY: Record<Exclude<Reason, "keyword">, string> = {
  no_keyword: "No keyword match",
  template: "Template ad",
  claude_off_topic: "Claude: off-topic",
  ad_override: "You excluded this ad",
  advertiser_override: "You excluded this advertiser",
};

export type OverrideCall =
  | { kind: "set"; scope: "ad" | "advertiser"; target_id: string; mode: "exclude" | "include" }
  | { kind: "clear"; scope: "ad" | "advertiser"; target_id: string };

/**
 * The override calls that include an excluded ad: clear what the user set,
 * force-include what the keyword filter dropped.
 */
export function includeCall(ad: AdView): OverrideCall {
  switch (ad.reason) {
    case "advertiser_override":
      return { kind: "clear", scope: "advertiser", target_id: advertiserKey(ad) };
    case "ad_override":
      return { kind: "clear", scope: "ad", target_id: ad.ad_archive_id };
    default:
      return { kind: "set", scope: "ad", target_id: ad.ad_archive_id, mode: "include" };
  }
}

/** One call per distinct target (an excluded advertiser covers many ads). */
export function includeCalls(ads: AdView[]): OverrideCall[] {
  const seen = new Map<string, OverrideCall>();
  for (const ad of ads) {
    const call = includeCall(ad);
    seen.set(`${call.scope}:${call.target_id}`, call);
  }
  return [...seen.values()];
}

export function excludeCalls(ads: AdView[]): OverrideCall[] {
  return ads.map((ad) => ({
    kind: "set",
    scope: "ad",
    target_id: ad.ad_archive_id,
    mode: "exclude",
  }));
}

function listNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * What a toast and the toggle's screen-reader label call the target:
 * `ad from Northbrace`, `3 ads from Northbrace`, `4 ads from 3 advertisers`, and,
 * when an advertiser-level override is cleared (which brings back every ad
 * from that advertiser, not just this creative's), `all ads from Northbrace` or
 * `2 ads and all ads from Northbrace`.
 */
export function toggleSubject(ads: AdView[], calls: OverrideCall[]): string {
  const advertiserKeys = new Set(
    calls.filter((c) => c.scope === "advertiser").map((c) => c.target_id),
  );
  const byAdvertiser = ads.filter((ad) => advertiserKeys.has(advertiserKey(ad)));
  const byAd = ads.filter((ad) => !advertiserKeys.has(advertiserKey(ad)));
  const advertiserNames = [...new Set(byAdvertiser.map(advertiserName))];
  const allFrom = advertiserNames.length > 0 ? `all ads from ${listNames(advertiserNames)}` : "";

  if (byAd.length === 0) return allFrom || "ad";
  const count = byAd.length === 1 ? "ad" : `${byAd.length} ads`;
  if (allFrom) return `${count} and ${allFrom}`;
  const names = new Set(byAd.map(advertiserName));
  const from = names.size === 1 ? advertiserName(byAd[0] as AdView) : `${names.size} advertisers`;
  return `${count} from ${from}`;
}

/** The distinct reasons a group's ads are excluded for, the lead's first. */
export function groupReasons(group: CreativeGroup): Exclude<Reason, "keyword">[] {
  const reasons = new Set<Reason>([group.lead.reason, ...group.ads.map((ad) => ad.reason)]);
  reasons.delete("keyword");
  return [...reasons] as Exclude<Reason, "keyword">[];
}

/** `https://{domain}` when the domain is a plain hostname, else null (no link). */
export function landingHref(domain: string | null): string | null {
  if (!domain || !/^[a-z0-9.-]+$/i.test(domain)) return null;
  try {
    const url = new URL(`https://${domain}`);
    return url.hostname === domain.toLowerCase() ? url.href : null;
  } catch {
    return null;
  }
}
