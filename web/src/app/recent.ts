import type { Snapshot } from "@/api/client";

/** The search a snapshot ran: an advertiser's page, or a keyword, in one country. */
function searchKey(snapshot: Snapshot): string {
  const target =
    snapshot.kind === "advertiser" && snapshot.page_id
      ? `page:${snapshot.page_id}`
      : `q:${snapshot.query.trim().toLowerCase()}`;
  return `${snapshot.kind}|${target}|${snapshot.country}`;
}

/**
 * The rail's recent searches: the newest snapshot of each search only. The API
 * lists newest first, so the first one seen per search wins. Older runs (daily
 * watch checks among them) stay reachable from the watchlist and compare.
 */
export function latestPerSearch(snapshots: Snapshot[]): Snapshot[] {
  const seen = new Set<string>();
  return snapshots.filter((snapshot) => {
    const key = searchKey(snapshot);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
