import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  type AnalysisJob,
  type CancelResponse,
  type CompareColumn,
  type Report,
  type SearchEvent,
  type SearchQuery,
  type Settings,
  type SettingsUpdate,
  type Snapshot,
  type StartSearchResponse,
  type MarkReadBody,
  type MarkReadResponse,
  type RunNowResponse,
  type WatchAlert,
  type WatchItem,
  type WatchTrend,
} from "./client";
import { DEFAULT_TIMEZONE } from "@/lib/format";

export const queryKeys = {
  settings: ["settings"] as const,
  snapshots: (keyword?: string) =>
    keyword === undefined ? (["snapshots"] as const) : (["snapshots", keyword] as const),
  report: (id: number) => ["report", id] as const,
  activeSearch: ["searches", "active"] as const,
  analysis: (id: number) => ["analysis", id] as const,
  watch: ["watch"] as const,
  trend: (id: number) => ["watch", id, "trend"] as const,
  alerts: ["alerts"] as const,
  compare: (ids: number[]) => ["compare", ids.join(",")] as const,
};

/** How often a running analysis job is polled. */
export const ANALYSIS_POLL_MS = 2000;

export function useSettings() {
  return useQuery({
    queryKey: queryKeys.settings,
    queryFn: () => api.get<Settings>("/api/settings"),
    staleTime: Infinity,
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: SettingsUpdate) => api.put<Settings>("/api/settings", patch),
    onSuccess: (settings) => {
      queryClient.setQueryData(queryKeys.settings, settings);
      // A new daily time moves every item's next check.
      void queryClient.invalidateQueries({ queryKey: queryKeys.watch });
    },
  });
}

/** The timezone to format dates in; the default until settings arrive. */
export function useTimezone(): string {
  return useSettings().data?.timezone ?? DEFAULT_TIMEZONE;
}

export function useSnapshots(keyword?: string) {
  return useQuery({
    queryKey: queryKeys.snapshots(keyword),
    queryFn: () =>
      api.get<Snapshot[]>(
        keyword === undefined
          ? "/api/snapshots"
          : `/api/snapshots?keyword=${encodeURIComponent(keyword)}`,
      ),
  });
}

export function useReport(id: number | undefined) {
  return useQuery({
    queryKey: queryKeys.report(id ?? -1),
    queryFn: () => api.get<Report>(`/api/snapshots/${id}/report`),
    enabled: id !== undefined && Number.isInteger(id),
  });
}

/** 2 to 4 snapshots side by side, in the order given. Fewer than 2 fetch nothing. */
export function useCompare(ids: number[]) {
  return useQuery({
    queryKey: queryKeys.compare(ids),
    queryFn: () => api.get<CompareColumn[]>(`/api/compare?ids=${ids.join(",")}`),
    enabled: ids.length >= 2,
    // Adding or removing a column keeps the others on screen while it loads.
    placeholderData: keepPreviousData,
  });
}

/**
 * The snapshot's latest analysis job (or null), polled every 2 s while it runs.
 * A failed job stays here until a newer analysis exists.
 */
export function useAnalysisJob(id: number) {
  return useQuery({
    queryKey: queryKeys.analysis(id),
    queryFn: () => api.get<AnalysisJob | null>(`/api/snapshots/${id}/analysis`),
    refetchInterval: (query) => (query.state.data?.state === "running" ? ANALYSIS_POLL_MS : false),
  });
}

/** Starts (or joins) the snapshot's analysis job; the poll above takes it from there. */
export function useStartAnalysis(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (force: boolean) =>
      api.post<AnalysisJob>(`/api/snapshots/${id}/analysis`, { force }),
    onSuccess: (job) => queryClient.setQueryData(queryKeys.analysis(id), job),
  });
}

export function useActiveSearch() {
  return useQuery({
    queryKey: queryKeys.activeSearch,
    queryFn: () => api.get<SearchEvent | null>("/api/searches/active"),
  });
}

export function useStartSearch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (query: SearchQuery) => api.post<StartSearchResponse>("/api/searches", query),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.snapshots() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.activeSearch });
    },
  });
}

export function useCancelSearch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (snapshotId: number) =>
      api.post<CancelResponse>(`/api/searches/${snapshotId}/cancel`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.activeSearch });
    },
  });
}

export function useOpenBrowser() {
  return useMutation({
    mutationFn: () => api.post<void>("/api/browser/open"),
  });
}

const EVENT_KINDS = ["progress", "media", "done", "blocked", "error", "cancelled"] as const;
const TERMINAL_KINDS: ReadonlySet<SearchEvent["kind"]> = new Set([
  "done",
  "blocked",
  "error",
  "cancelled",
]);

export type SearchStreamStatus = "idle" | "connecting" | "open" | "closed" | "lost";

export interface SearchStream {
  /** The latest event, or undefined before the first one arrives. */
  event: SearchEvent | undefined;
  status: SearchStreamStatus;
}

interface StreamState extends SearchStream {
  id: number | undefined;
}

export function isTerminal(event: SearchEvent | undefined): boolean {
  return event !== undefined && TERMINAL_KINDS.has(event.kind);
}

/**
 * Live events for one search over SSE. The stream closes itself after a
 * terminal event (done, blocked, error, cancelled) and refreshes the
 * snapshot list and the report.
 */
export function useSearchEvents(id: number | undefined): SearchStream {
  const queryClient = useQueryClient();
  const [state, setState] = useState<StreamState>({
    id: undefined,
    event: undefined,
    status: "idle",
  });

  useEffect(() => {
    if (id === undefined) return;
    const source = new EventSource(`/api/searches/${id}/events`);
    let finished = false;

    const onEvent = (message: MessageEvent<string>) => {
      let event: SearchEvent;
      try {
        event = JSON.parse(message.data) as SearchEvent;
      } catch {
        return;
      }
      const terminal = TERMINAL_KINDS.has(event.kind);
      setState({ id, event, status: terminal ? "closed" : "open" });
      if (terminal) {
        finished = true;
        source.close();
        void queryClient.invalidateQueries({ queryKey: queryKeys.snapshots() });
        void queryClient.invalidateQueries({ queryKey: queryKeys.report(id) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.activeSearch });
      }
    };

    for (const kind of EVENT_KINDS) source.addEventListener(kind, onEvent);
    source.onerror = () => {
      if (finished) return;
      // EventSource reconnects on its own while CONNECTING; CLOSED is final.
      if (source.readyState === EventSource.CLOSED) {
        setState((prev) => ({
          id,
          event: prev.id === id ? prev.event : undefined,
          status: "lost",
        }));
      }
    };

    return () => {
      finished = true;
      source.close();
    };
  }, [id, queryClient]);

  if (id === undefined) return { event: undefined, status: "idle" };
  if (state.id !== id) return { event: undefined, status: "connecting" };
  return { event: state.event, status: state.status };
}

/** How often the watchlist refreshes while a check runs, and otherwise. */
export const WATCH_POLL_RUNNING_MS = 4000;
export const WATCH_POLL_IDLE_MS = 60_000;

/** Every watch item. A fake or older API can answer null: that reads as none. */
export function useWatchItems() {
  return useQuery({
    queryKey: queryKeys.watch,
    queryFn: async () => (await api.get<WatchItem[] | null>("/api/watch")) ?? [],
    refetchInterval: (query) =>
      query.state.data?.some((item) => item.running) ? WATCH_POLL_RUNNING_MS : WATCH_POLL_IDLE_MS,
  });
}

export function useWatchTrend(id: number) {
  return useQuery({
    queryKey: queryKeys.trend(id),
    queryFn: () => api.get<WatchTrend>(`/api/watch/${id}/trend`),
    refetchInterval: (query) =>
      query.state.data?.item.running ? WATCH_POLL_RUNNING_MS : WATCH_POLL_IDLE_MS,
  });
}

function useInvalidateWatch() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.watch });
    void queryClient.invalidateQueries({ queryKey: queryKeys.alerts });
  };
}

export function useAddWatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (query: SearchQuery) => api.post<WatchItem>("/api/watch", query),
    onSuccess: (item) => {
      queryClient.setQueryData<WatchItem[]>(queryKeys.watch, (items) =>
        items === undefined ? undefined : [...items.filter((i) => i.id !== item.id), item],
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.watch });
    },
  });
}

export function useSetWatchEnabled() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.patch<WatchItem>(`/api/watch/${id}`, { enabled }),
    onMutate: ({ id, enabled }) => {
      queryClient.setQueryData<WatchItem[]>(queryKeys.watch, (items) =>
        items?.map((i) => (i.id === id ? { ...i, enabled } : i)),
      );
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watch }),
  });
}

export function useRemoveWatch() {
  const queryClient = useQueryClient();
  const invalidate = useInvalidateWatch();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/watch/${id}`),
    onSuccess: (_, id) => {
      queryClient.setQueryData<WatchItem[]>(queryKeys.watch, (items) =>
        items?.filter((i) => i.id !== id),
      );
      queryClient.removeQueries({ queryKey: queryKeys.trend(id) });
      invalidate();
    },
  });
}

export function useRunWatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<RunNowResponse>(`/api/watch/${id}/run`),
    onSuccess: (_, id) => {
      // Shows "Checking" straight away; the poll takes it from there.
      queryClient.setQueryData<WatchItem[]>(queryKeys.watch, (items) =>
        items?.map((i) => (i.id === id ? { ...i, running: true } : i)),
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.watch });
      void queryClient.invalidateQueries({ queryKey: queryKeys.snapshots() });
    },
  });
}

/** How often alerts are re-read, so the rail badge picks up the daily check. */
export const ALERTS_POLL_MS = 60_000;

/** Every alert, newest first (the badge counts the unread ones). */
export function useAlerts() {
  return useQuery({
    queryKey: queryKeys.alerts,
    queryFn: async () => (await api.get<WatchAlert[] | null>("/api/alerts")) ?? [],
    refetchInterval: ALERTS_POLL_MS,
  });
}

export function useMarkAlertsRead() {
  const queryClient = useQueryClient();
  const invalidate = useInvalidateWatch();
  return useMutation({
    mutationFn: (body: MarkReadBody) => api.post<MarkReadResponse>("/api/alerts/read", body),
    onMutate: (body) => {
      queryClient.setQueryData<WatchAlert[]>(queryKeys.alerts, (alerts) =>
        alerts?.map((a) => (body.all || body.ids?.includes(a.id) ? { ...a, read: true } : a)),
      );
    },
    onSettled: invalidate,
  });
}
