import { useMemo, useState } from "react";

import { useTimezone } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatInt } from "@/lib/format";

import { CreativeTile, type TileMode } from "./CreativeTile";
import {
  advertiserKey,
  advertiserName,
  filterGroups,
  SORT_LABELS,
  type GalleryFilters,
  sortGroups,
  type CreativeGroup,
  type SortKey,
} from "./gallery";

/** Tiles rendered per step: whole rows at 4, 3 and 2 columns. */
const PAGE = 24;
const ALL = "all";

interface CreativeGalleryProps {
  groups: CreativeGroup[];
  mode: TileMode;
  /** The group whose override is in flight, by creative key. */
  pendingKey: string | null;
  onToggle: (group: CreativeGroup) => void;
  /** Shown when the tab has no creatives at all. */
  emptyText: string;
  filters: GalleryFilters;
  onFiltersChange: (filters: GalleryFilters) => void;
}

export function CreativeGallery({
  groups,
  mode,
  pendingKey,
  onToggle,
  emptyText,
  filters,
  onFiltersChange,
}: CreativeGalleryProps) {
  const timezone = useTimezone();
  const { sort, advertiser, media } = filters;
  const update = (next: Partial<GalleryFilters>) => onFiltersChange({ ...filters, ...next });
  const [limit, setLimit] = useState(PAGE);

  const advertisers = useMemo(() => advertiserOptions(groups), [groups]);
  // An advertiser that left this tab (excluded or included) no longer filters it.
  const activeAdvertiser = advertisers.some((a) => a.key === advertiser) ? advertiser : ALL;
  const shown = useMemo(
    () =>
      sortGroups(
        filterGroups(groups, activeAdvertiser === ALL ? null : activeAdvertiser, media),
        sort,
      ),
    [groups, activeAdvertiser, media, sort],
  );
  const filtered = activeAdvertiser !== ALL || media !== "all";

  if (groups.length === 0) {
    return <p className="border border-rule bg-panel px-5 py-8 text-13 text-muted">{emptyText}</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <label className="flex items-center gap-2 text-12 text-muted">
          <span id={`${mode}-sort-label`}>Sort</span>
          <Select value={sort} onValueChange={(v) => update({ sort: v as SortKey })}>
            <SelectTrigger aria-labelledby={`${mode}-sort-label`} className="min-w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
                <SelectItem key={key} value={key}>
                  {SORT_LABELS[key]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>

        <label className="flex min-w-0 items-center gap-2 text-12 text-muted">
          <span id={`${mode}-advertiser-label`}>Advertiser</span>
          <Select value={activeAdvertiser} onValueChange={(v) => update({ advertiser: v })}>
            <SelectTrigger aria-labelledby={`${mode}-advertiser-label`} className="w-56 min-w-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="max-h-80">
              <SelectItem value={ALL}>All advertisers</SelectItem>
              {advertisers.map((a) => (
                <SelectItem key={a.key} value={a.key}>
                  {a.name} <span className="num ml-1 text-12 text-muted">{a.count}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>

        <ToggleGroup
          type="single"
          value={media}
          onValueChange={(next) => {
            if (next === "all" || next === "image" || next === "video") update({ media: next });
          }}
          aria-label="Media type"
        >
          <ToggleGroupItem value="all">All media</ToggleGroupItem>
          <ToggleGroupItem value="image">Images</ToggleGroupItem>
          <ToggleGroupItem value="video">Videos</ToggleGroupItem>
        </ToggleGroup>

        <p className="ml-auto text-12 text-muted" aria-live="polite">
          {filtered ? (
            <>
              <span className="num text-text">{formatInt(shown.length)}</span> of{" "}
              <span className="num">{formatInt(groups.length)}</span> creatives
            </>
          ) : (
            <>
              <span className="num text-text">{formatInt(groups.length)}</span>{" "}
              {groups.length === 1 ? "creative" : "creatives"}
            </>
          )}
        </p>
      </div>

      {shown.length === 0 ? (
        <div className="flex items-center justify-between gap-4 border border-rule bg-panel px-5 py-6">
          <p className="text-13 text-muted">No creatives match these filters.</p>
          <Button variant="outline" onClick={() => update({ advertiser: ALL, media: "all" })}>
            Clear filters
          </Button>
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {shown.slice(0, limit).map((group) => (
            <CreativeTile
              key={group.key}
              group={group}
              mode={mode}
              timezone={timezone}
              pending={pendingKey === group.key}
              onToggle={onToggle}
            />
          ))}
        </ul>
      )}

      {shown.length > limit ? (
        <div className="flex items-center justify-center gap-4 pt-1">
          <Button variant="outline" onClick={() => setLimit((n) => n + PAGE)}>
            Show {formatInt(Math.min(PAGE, shown.length - limit))} more
          </Button>
          <p className="text-12 text-muted">
            <span className="num">{formatInt(shown.length - limit)}</span> not shown
          </p>
        </div>
      ) : null}
    </div>
  );
}

interface AdvertiserOption {
  key: string;
  name: string;
  count: number;
}

/** Advertisers with creatives in this tab, most creatives first. */
function advertiserOptions(groups: CreativeGroup[]): AdvertiserOption[] {
  const byKey = new Map<string, AdvertiserOption>();
  for (const group of groups) {
    const seen = new Set<string>();
    for (const ad of group.ads) {
      const key = advertiserKey(ad);
      if (seen.has(key)) continue;
      seen.add(key);
      const option = byKey.get(key) ?? { key, name: advertiserName(ad), count: 0 };
      option.count += 1;
      byKey.set(key, option);
    }
  }
  return [...byKey.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}
