import { useCallback, useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError, type Report } from "@/api/client";
import { queryKeys, useAnalysisJob, useStartAnalysis } from "@/api/hooks";

const FAILED = "Angle analysis failed.";

/**
 * The snapshot's analysis job. Starting one posts and hands over to the 2 s
 * poll; when a job this page watched running finishes, the report refreshes
 * (done) or a toast says why (error), with `Try again`. This also picks up an
 * auto-analysis that started after the search.
 *
 * `Try again` never posts directly: it calls `onRetry`, which opens the same
 * confirm dialog as the Analyze button (a run spends the user's quota).
 */
export function useAnalysis(report: Report, onRetry: (force: boolean) => void) {
  const id = report.snapshot.id;
  const queryClient = useQueryClient();
  const job = useAnalysisJob(id);
  const { mutate, isPending } = useStartAnalysis(id);
  // A re-run replaces a cached result for the same input; a first run may reuse one.
  const force = report.analysis !== null;
  // Latest callback without re-running the job effect when the parent re-renders.
  const retryRef = useRef(onRetry);
  useEffect(() => {
    retryRef.current = onRetry;
  });

  const start = useCallback(
    (rerun: boolean) => {
      mutate(rerun, {
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.detail : error.message, {
            action: { label: "Try again", onClick: () => retryRef.current(rerun) },
          }),
      });
    },
    [mutate],
  );

  const state = job.data?.state;
  const message = job.data?.message;
  const previous = useRef(state);

  useEffect(() => {
    const was = previous.current;
    previous.current = state;
    if (was !== "running") return;
    if (state === "done") {
      void queryClient.invalidateQueries({ queryKey: queryKeys.report(id) });
    } else if (state === "error") {
      toast.error(message ?? FAILED, {
        action: { label: "Try again", onClick: () => retryRef.current(force) },
      });
    }
  }, [state, message, id, force, queryClient]);

  return {
    job: job.data ?? null,
    running: state === "running",
    starting: isPending,
    force,
    start,
  };
}
