import type { ReactNode } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { ApiError, type SearchEvent } from "@/api/client";
import { useCancelSearch, useOpenBrowser, type SearchStreamStatus } from "@/api/hooks";
import { Button, buttonVariants } from "@/components/ui/button";
import { formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

import { splitBlocked } from "./form";
import { DisplayInt, Numerals } from "./Numerals";

function errorDetail(error: Error): string {
  return error instanceof ApiError ? error.detail : error.message;
}

interface LiveProgressProps {
  snapshotId: number;
  /** The latest event; undefined until the first one arrives. */
  event: SearchEvent | undefined;
  streamStatus: SearchStreamStatus;
  /** What was searched, e.g. `posture corrector` in `US`, when known. */
  query: { keyword: string; country: string } | undefined;
  onSearchAgain: () => void;
}

const BIG_NUMBER = "num text-40 font-medium tracking-[-0.05em]";

type Tone = "running" | "blocked" | "error" | "cancelled" | "done";

const EDGE: Record<Tone, string> = {
  running: "bg-rule",
  done: "bg-pass",
  blocked: "bg-warn",
  error: "bg-fail",
  cancelled: "bg-muted",
};

export function LiveProgress({
  snapshotId,
  event,
  streamStatus,
  query,
  onSearchAgain,
}: LiveProgressProps) {
  const cancel = useCancelSearch();
  const openBrowser = useOpenBrowser();

  const kind = event?.kind ?? "progress";
  const tone: Tone = kind === "progress" || kind === "media" ? "running" : kind;
  const running = tone === "running";
  const ads = event?.ads_found ?? 0;
  const advertisers = event?.advertisers ?? 0;
  const phase = event?.phase ?? "Opening the Ad Library";
  const detail = running ? event?.message : null;

  return (
    <section
      aria-labelledby="progress-heading"
      aria-busy={running}
      className="relative border border-rule bg-panel"
    >
      {/* The top edge carries the state: a moving scan while running, a colour after. */}
      <div className={cn("relative h-0.5 overflow-hidden", EDGE[tone])} aria-hidden="true">
        {running ? (
          <div className="progress-scan absolute inset-y-0 left-0 w-1/3 bg-signal motion-reduce:w-full" />
        ) : null}
      </div>

      {/* Always mounted, so the end of the search is announced as well as its phases. */}
      <p role="status" aria-live="polite" aria-label="Search status" className="sr-only">
        {liveText(kind, phase)}
      </p>

      <div className="flex flex-col gap-6 px-5 pt-4 pb-5 md:px-6">
        <header className="flex items-baseline justify-between gap-4">
          <h2 id="progress-heading" className="min-w-0 truncate text-13 text-muted">
            {query ? (
              <>
                <span className="font-medium text-text">{query.keyword}</span>
                <span className="ml-3">{query.country}</span>
              </>
            ) : (
              "Current search"
            )}
            <span className="num ml-3 text-12">#{snapshotId}</span>
          </h2>
          {running ? (
            <Button
              variant="outline"
              size="sm"
              disabled={cancel.isPending || cancel.isSuccess}
              onClick={() =>
                cancel.mutate(snapshotId, {
                  onError: (error) => toast.error(errorDetail(error)),
                })
              }
            >
              {cancel.isPending || cancel.isSuccess ? "Cancelling" : "Cancel"}
            </Button>
          ) : null}
        </header>

        {/* A stopped search with nothing found has no count worth headlining. */}
        {running || ads > 0 ? (
          <p className="text-24 leading-[44px] text-text">
            Found <DisplayInt className={BIG_NUMBER} text={formatInt(ads)} />{" "}
            {ads === 1 ? "ad" : "ads"} from{" "}
            <DisplayInt className={BIG_NUMBER} text={formatInt(advertisers)} />{" "}
            {advertisers === 1 ? "advertiser" : "advertisers"}
          </p>
        ) : null}

        {running ? (
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-t border-rule pt-3 text-13">
            <span className="text-text">{phase}</span>
            {detail ? (
              <span className="text-muted">
                <Numerals text={detail} />
              </span>
            ) : null}
            {streamStatus === "lost" ? (
              <span className="text-warn">
                Lost the live connection. Reload the page to re-attach.
              </span>
            ) : null}
          </div>
        ) : null}

        {kind === "blocked" ? (
          <BlockedNotice
            message={event?.message}
            opening={openBrowser.isPending}
            onOpenBrowser={() =>
              openBrowser.mutate(undefined, {
                onSuccess: () =>
                  toast("The AdScout browser is open. Sign in, close it, then search again."),
                onError: (error) => toast.error(errorDetail(error)),
              })
            }
          />
        ) : null}

        {kind === "error" ? (
          <Outcome tone="error" title={event?.message ?? "The search failed."}>
            <Button variant="outline" onClick={onSearchAgain}>
              Search again
            </Button>
          </Outcome>
        ) : null}

        {kind === "cancelled" ? (
          <Outcome tone="cancelled" title="Search cancelled. Partial results were saved.">
            <Link to={`/report/${snapshotId}`} className={buttonVariants({ variant: "outline" })}>
              Open report
            </Link>
          </Outcome>
        ) : null}

        {kind === "done" ? (
          <Outcome tone="done" title="Done. Opening the report.">
            <Link to={`/report/${snapshotId}`} className={buttonVariants({ variant: "outline" })}>
              Open report
            </Link>
          </Outcome>
        ) : null}
      </div>
    </section>
  );
}

/** What the live region says: the phase while running, then how the search ended. */
function liveText(kind: SearchEvent["kind"], phase: string): string {
  switch (kind) {
    case "done":
      return "Search done.";
    case "blocked":
      return "Search blocked.";
    case "error":
      return "Search failed.";
    case "cancelled":
      return "Search cancelled.";
    default:
      return `${phase}.`;
  }
}

function BlockedNotice({
  message,
  opening,
  onOpenBrowser,
}: {
  message: string | null | undefined;
  opening: boolean;
  onOpenBrowser: () => void;
}) {
  const { reason, fix } = splitBlocked(message);
  return (
    <Outcome tone="blocked" title={reason} body={fix}>
      <Button onClick={onOpenBrowser} disabled={opening}>
        Open browser
      </Button>
    </Outcome>
  );
}

const TITLE_TONE: Record<Exclude<Tone, "running">, string> = {
  done: "text-text",
  blocked: "text-warn",
  error: "text-fail",
  cancelled: "text-text",
};

function Outcome({
  tone,
  title,
  body,
  children,
}: {
  tone: Exclude<Tone, "running">;
  title: string;
  body?: string;
  children: ReactNode;
}) {
  return (
    <div
      role={tone === "blocked" || tone === "error" ? "alert" : "status"}
      className="flex flex-col gap-4 border-t border-rule pt-4 md:flex-row md:items-start md:justify-between"
    >
      <div className="max-w-[68ch] space-y-1">
        <p className={cn("text-15 font-medium", TITLE_TONE[tone])}>{title}</p>
        {body ? <p className="text-13 text-muted">{body}</p> : null}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}
