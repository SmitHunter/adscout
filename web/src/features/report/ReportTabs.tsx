import { useMemo, useState } from "react";

import type { Report } from "@/api/client";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AdvertiserWatchButton } from "@/features/watch/WatchButton";
import { formatInt } from "@/lib/format";

import { AdvertiserTable } from "./AdvertiserTable";
import { CreativeGallery } from "./CreativeGallery";
import {
  DEFAULT_FILTERS,
  excludeCalls,
  groupCreatives,
  includeCalls,
  toggleSubject,
  type AdvertiserRow,
  type CreativeGroup,
  type GalleryFilters,
} from "./gallery";
import { useRelevance } from "./useRelevance";

export type Tab = "creatives" | "advertisers" | "excluded";

/**
 * Creatives, advertisers and exclusions below the timeline. The active tab is
 * state above the report refetch a toggle triggers, so it survives it: here,
 * or in the page when it passes `tab` (so other panels can open a tab).
 */
export function ReportTabs({
  report,
  tab: controlled,
  onTabChange,
}: {
  report: Report;
  tab?: Tab;
  onTabChange?: (tab: Tab) => void;
}) {
  const [own, setOwn] = useState<Tab>("creatives");
  const tab = controlled ?? own;
  const setTab = onTabChange ?? setOwn;
  const relevance = useRelevance(report);
  // Here, not in the galleries: a gallery unmounts when its tab is left.
  const [relevantFilters, setRelevantFilters] = useState<GalleryFilters>(DEFAULT_FILTERS);
  const [excludedFilters, setExcludedFilters] = useState<GalleryFilters>(DEFAULT_FILTERS);

  const relevant = useMemo(
    () => groupCreatives(report.ads.filter((ad) => ad.relevant)),
    [report.ads],
  );
  const excluded = useMemo(
    () => groupCreatives(report.ads.filter((ad) => !ad.relevant)),
    [report.ads],
  );
  const advertisers = report.advertisers;

  const toggleGroup = (mode: "relevant" | "excluded") => (group: CreativeGroup) => {
    const calls = mode === "relevant" ? excludeCalls(group.ads) : includeCalls(group.ads);
    void relevance.run(
      `creative:${group.key}`,
      mode === "relevant" ? "Excluded" : "Included",
      toggleSubject(group.ads, calls),
      calls,
    );
  };

  const toggleAdvertiser = (row: AdvertiserRow) => {
    void relevance.run(
      `advertiser:${row.key}`,
      row.excluded ? "Included" : "Excluded",
      row.name,
      row.excluded
        ? [{ kind: "clear", scope: "advertiser", target_id: row.key }]
        : [{ kind: "set", scope: "advertiser", target_id: row.key, mode: "exclude" }],
    );
  };

  const pendingCreative = relevance.pending?.startsWith("creative:")
    ? relevance.pending.slice("creative:".length)
    : null;
  const pendingAdvertiser = relevance.pending?.startsWith("advertiser:")
    ? relevance.pending.slice("advertiser:".length)
    : null;

  return (
    <Tabs
      value={tab}
      onValueChange={(next) => setTab(next as Tab)}
      className="scroll-mt-4 gap-4"
      id="report-tabs"
    >
      <TabsList aria-label="Report detail" className="gap-6">
        <TabsTrigger value="creatives">
          Creatives <Count n={relevant.length} />
        </TabsTrigger>
        <TabsTrigger value="advertisers">
          Advertisers <Count n={advertisers.length} />
        </TabsTrigger>
        <TabsTrigger value="excluded">
          Excluded <Count n={excluded.length} />
        </TabsTrigger>
      </TabsList>

      <TabsContent value="creatives">
        <CreativeGallery
          groups={relevant}
          mode="relevant"
          pendingKey={pendingCreative}
          onToggle={toggleGroup("relevant")}
          filters={relevantFilters}
          onFiltersChange={setRelevantFilters}
          emptyText="Every ad is excluded. Include ads from the Excluded tab to score them."
        />
      </TabsContent>
      <TabsContent value="advertisers">
        <AdvertiserTable
          rows={advertisers}
          pendingKey={pendingAdvertiser}
          onToggle={toggleAdvertiser}
          renderWatch={(row) => (
            <AdvertiserWatchButton
              pageId={row.key}
              name={row.name}
              country={report.snapshot.country}
            />
          )}
        />
      </TabsContent>
      <TabsContent value="excluded">
        <CreativeGallery
          groups={excluded}
          mode="excluded"
          pendingKey={pendingCreative}
          onToggle={toggleGroup("excluded")}
          filters={excludedFilters}
          onFiltersChange={setExcludedFilters}
          emptyText={`Nothing is excluded. Every ad matches "${report.snapshot.query}".`}
        />
      </TabsContent>
    </Tabs>
  );
}

/** `(65)`: the parentheses stay in the tab's accessible name. */
function Count({ n }: { n: number }) {
  return (
    <span className="text-muted">
      (<span className="num text-12">{formatInt(n)}</span>)
    </span>
  );
}
