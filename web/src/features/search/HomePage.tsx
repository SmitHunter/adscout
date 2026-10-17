import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { ApiError, type SearchQuery } from "@/api/client";
import {
  isTerminal,
  queryKeys,
  useActiveSearch,
  useSearchEvents,
  useSettings,
  useSnapshots,
  useStartSearch,
} from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDemo } from "@/features/demo/demo";

import { LiveProgress } from "./LiveProgress";
import { RecentSearches } from "./RecentSearches";
import { DEFAULT_FORM, type SearchForm } from "./form";
import { SearchBar } from "./SearchBar";

const SUGGESTION: SearchForm = { ...DEFAULT_FORM, keyword: "posture corrector", country: "US" };
const MAX_LIMIT = 1000;

interface Run {
  id: number;
  /** The query, when this page started it; unknown after a reload. */
  form?: SearchForm;
}

export function HomePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const settings = useSettings();
  const snapshots = useSnapshots();
  const active = useActiveSearch();
  const start = useStartSearch();
  const inputRef = useRef<HTMLInputElement>(null);

  const [form, setForm] = useState<SearchForm>(DEFAULT_FORM);
  const [run, setRun] = useState<Run | null>(null);
  const [attachedId, setAttachedId] = useState<number | undefined>(undefined);

  // Re-attach to a search that is already running (a reload, or another tab).
  const activeEvent = active.data ?? undefined;
  if (activeEvent !== undefined && activeEvent.snapshot_id !== attachedId) {
    setAttachedId(activeEvent.snapshot_id);
    if (run?.id !== activeEvent.snapshot_id) setRun({ id: activeEvent.snapshot_id });
  }

  const stream = useSearchEvents(run?.id);
  const event = stream.event ?? (activeEvent?.snapshot_id === run?.id ? activeEvent : undefined);
  const running = run !== null && !isTerminal(event);

  useEffect(() => {
    if (event?.kind === "done") void navigate(`/report/${event.snapshot_id}`);
  }, [event, navigate]);

  const busy = start.isPending || running;
  const demo = useDemo();

  const submit = (next: SearchForm) => {
    if (busy || demo) return;
    const keyword = next.keyword.trim();
    if (keyword === "") {
      inputRef.current?.focus();
      return;
    }
    const query: SearchQuery = {
      ...next,
      kind: "keyword",
      keyword,
      limit: Math.min(settings.data?.max_results ?? 300, MAX_LIMIT),
    };
    start.mutate(query, {
      onSuccess: ({ snapshot_id }) => setRun({ id: snapshot_id, form: { ...next, keyword } }),
      onError: (error) => {
        toast.error(error instanceof ApiError ? error.detail : error.message);
        // Busy because of a search this server started elsewhere (another tab or
        // window; CLI scrapes don't register with the server): refetch the active
        // search so this page attaches to its progress.
        if (error instanceof ApiError && error.status === 409) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.activeSearch });
        }
      },
    });
  };

  const runQuery =
    run?.form ??
    (() => {
      const snap = snapshots.data?.find((s) => s.id === run?.id);
      return snap ? { keyword: snap.query, country: snap.country } : undefined;
    })();

  const isEmpty = snapshots.isSuccess && snapshots.data.length === 0 && run === null;

  return (
    <div className="space-y-8">
      {isEmpty ? (
        <h1 className="max-w-[22ch] pt-8 text-40 font-semibold tracking-[-0.015em] text-text">
          Search a product to see if it's a proven winner.
        </h1>
      ) : (
        <h1 className="sr-only">New search</h1>
      )}

      <div className="space-y-4">
        <SearchBar
          value={form}
          onChange={setForm}
          onSubmit={submit}
          busy={busy}
          euAvailable={settings.data?.has_meta_token ?? false}
          demo={demo}
          inputRef={inputRef}
        />

        {isEmpty ? (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => {
              setForm(SUGGESTION);
              submit(SUGGESTION);
            }}
          >
            Try "posture corrector" in US
          </Button>
        ) : null}
      </div>

      {run !== null ? (
        <LiveProgress
          snapshotId={run.id}
          event={event}
          streamStatus={stream.status}
          query={runQuery}
          onSearchAgain={() => {
            if (run.form) {
              setForm(run.form);
              submit(run.form);
            } else {
              setRun(null);
              inputRef.current?.focus();
            }
          }}
        />
      ) : null}

      {snapshots.isPending ? (
        <div className="space-y-2" aria-busy="true" aria-label="Loading recent searches">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : null}

      {snapshots.isSuccess && snapshots.data.length > 0 ? (
        <RecentSearches snapshots={snapshots.data} />
      ) : null}

      {snapshots.isError ? (
        <p className="text-13 text-muted">
          Can't load recent searches.{" "}
          {snapshots.error instanceof ApiError ? snapshots.error.detail : snapshots.error.message}
        </p>
      ) : null}
    </div>
  );
}
