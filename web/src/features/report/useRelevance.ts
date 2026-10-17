import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, ApiError, type Report } from "@/api/client";
import { queryKeys } from "@/api/hooks";

import type { OverrideCall } from "./gallery";

export type Verb = "Excluded" | "Included";

function send(snapshotId: number, call: OverrideCall): Promise<void> {
  // The server keys overrides on the snapshot's search: its keyword, or its advertiser page.
  const target = { snapshot_id: snapshotId, scope: call.scope, target_id: call.target_id };
  return call.kind === "set"
    ? api.put<void>("/api/overrides", { ...target, mode: call.mode })
    : api.delete<void>("/api/overrides", target);
}

/** `Excluded 2 of 4 ads. {detail}`; `changes` when advertiser overrides are in the mix. */
export function partialMessage(verb: Verb, done: number, calls: OverrideCall[], detail: string) {
  if (done === 0) return detail;
  const unit = calls.every((c) => c.scope === "ad") ? "ads" : "changes";
  return `${verb} ${done} of ${calls.length} ${unit}. ${detail}`;
}

/**
 * Runs a toggle's override calls one at a time for the report's search, then
 * refreshes the report once, also after a failure part-way through, so the
 * page always shows what was saved. Toasts `Excluded {name}` /
 * `Included {name}`, or how far it got. The page keeps its tab (state lives
 * above the refetch) and its scroll position (restored here in case the
 * content above shifted while it reloaded).
 */
export function useRelevance(report: Report) {
  const queryClient = useQueryClient();
  const snapshotId = report.snapshot.id;
  const [pending, setPending] = useState<string | null>(null);
  // A ref, not the state: two clicks before a re-render must not both run.
  const busy = useRef(false);

  const run = async (id: string, verb: Verb, subject: string, calls: OverrideCall[]) => {
    if (busy.current || calls.length === 0) return;
    busy.current = true;
    const scroller = scrollContainer();
    const top = scroller.scrollTop;
    setPending(id);
    let done = 0;
    let failure: unknown = null;
    try {
      for (const call of calls) {
        await send(snapshotId, call);
        done += 1;
      }
    } catch (error) {
      failure = error;
    }
    try {
      await queryClient.invalidateQueries({ queryKey: queryKeys.report(snapshotId) });
    } finally {
      if (failure === null) {
        toast.success(`${verb} ${subject}`);
      } else {
        const detail = failure instanceof ApiError ? failure.detail : String(failure);
        toast.error(partialMessage(verb, done, calls, detail));
      }
      busy.current = false;
      setPending(null);
      requestAnimationFrame(() => {
        if (Math.abs(scroller.scrollTop - top) > 1) scroller.scrollTop = top;
      });
    }
  };

  return { pending, run };
}

/** The report scrolls inside the shell's <main> on desktop and the document on phones. */
function scrollContainer(): Element {
  const main = document.querySelector("main");
  if (
    main &&
    main.scrollHeight > main.clientHeight &&
    getComputedStyle(main).overflowY !== "visible"
  ) {
    return main;
  }
  return document.scrollingElement ?? document.documentElement;
}
