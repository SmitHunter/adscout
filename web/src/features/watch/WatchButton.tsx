import { useState } from "react";
import { CheckIcon, EyeIcon } from "lucide-react";
import { Link } from "react-router";
import { toast } from "sonner";

import { ApiError, type Snapshot } from "@/api/client";
import { useAddWatch, useRemoveWatch, useSettings, useWatchItems } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { RemoveWatchDialog } from "./RemoveWatchDialog";
import {
  advertiserTarget,
  findWatchItem,
  isPageId,
  targetOfSnapshot,
  watchQuery,
  type WatchTarget,
} from "./watch";

/** Watch `target`, or (after a confirm) stop watching it. */
function useWatchToggle(target: WatchTarget) {
  const items = useWatchItems();
  const settings = useSettings();
  const add = useAddWatch();
  const remove = useRemoveWatch();
  const [confirming, setConfirming] = useState(false);
  const item = findWatchItem(items.data, target);

  const watch = () =>
    add.mutate(watchQuery(target, settings.data?.max_results ?? 300), {
      onSuccess: (created) => toast.success(`Watching ${created.query} daily.`),
      onError: (err) => toast.error(err instanceof ApiError ? err.detail : err.message),
    });

  const stop = () => {
    if (item === undefined) return;
    remove.mutate(item.id, {
      onSuccess: () => toast.success(`Stopped watching ${item.query}.`),
      onError: (err) => toast.error(err instanceof ApiError ? err.detail : err.message),
    });
  };

  return {
    watching: item !== undefined,
    /** Watched, but its daily check is switched off. */
    paused: item !== undefined && !item.enabled ? item : null,
    ready: items.isSuccess,
    pending: add.isPending || remove.isPending,
    onClick: () => (item === undefined ? watch() : setConfirming(true)),
    dialog: (
      <RemoveWatchDialog
        query={confirming ? (item?.query ?? target.query) : null}
        onOpenChange={setConfirming}
        onConfirm={stop}
        action="Stop watching"
      />
    ),
  };
}

/** The report header's `Watch` / `Watching`: this report's search, checked daily. */
export function WatchButton({ snapshot }: { snapshot: Snapshot }) {
  const toggle = useWatchToggle(targetOfSnapshot(snapshot));
  return (
    <>
      <Button
        variant="outline"
        aria-pressed={toggle.watching}
        disabled={!toggle.ready || toggle.pending}
        onClick={toggle.onClick}
        className={cn(toggle.watching && "border-signal/60")}
      >
        {toggle.watching ? (
          <CheckIcon aria-hidden="true" className="text-signal" />
        ) : (
          <EyeIcon aria-hidden="true" />
        )}
        {toggle.watching ? "Watching" : "Watch"}
      </Button>
      {toggle.paused !== null ? (
        <Link
          to={`/watch/${toggle.paused.id}`}
          title="Daily checks are off for this search. Turn them on from its watch page."
          className="text-13 text-muted underline decoration-rule underline-offset-4 hover:text-text hover:decoration-muted"
        >
          Paused
        </Link>
      ) : null}
      {toggle.dialog}
    </>
  );
}

/** A row's `Watch` in the advertiser table: that advertiser's page, in this report's market. */
export function AdvertiserWatchButton({
  pageId,
  name,
  country,
}: {
  pageId: string;
  name: string;
  country: string;
}) {
  const toggle = useWatchToggle(advertiserTarget(pageId, name, country));
  if (!isPageId(pageId)) return null;
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        aria-pressed={toggle.watching}
        aria-label={`${toggle.watching ? "Watching" : "Watch"} ${name}`}
        disabled={!toggle.ready || toggle.pending}
        onClick={toggle.onClick}
        className={cn("w-[92px]", toggle.watching && "text-signal hover:text-signal")}
      >
        {toggle.watching ? <CheckIcon aria-hidden="true" /> : null}
        {toggle.watching ? "Watching" : "Watch"}
      </Button>
      {toggle.dialog}
    </>
  );
}
