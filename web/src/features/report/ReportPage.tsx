import { useState } from "react";
import { useNavigate, useParams, Link } from "react-router";
import { toast } from "sonner";

import { ApiError, type Report, type SearchQuery } from "@/api/client";
import { useReport, useSettings, useStartSearch, useTimezone } from "@/api/hooks";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/format";
import { useDemo } from "@/features/demo/demo";
import { DemoLock } from "@/features/demo/DemoLock";
import { forTheApp } from "@/features/search/form";
import { WatchButton } from "@/features/watch/WatchButton";
import { cn } from "@/lib/utils";

import { AnglesPanel } from "./AnglesPanel";
import { ExportMenu } from "./ExportMenu";
import { FactorPanel } from "./FactorRuler";
import { LaunchTimeline } from "./LaunchTimeline";
import { Notice } from "./Notice";
import { ReportTabs, type Tab } from "./ReportTabs";
import { ScoreBlock } from "./ScoreBlock";

/** `/report/42` → 42; anything else (`abc`, `1.5`, `-3`) → undefined. */
function parseId(raw: string | undefined): number | undefined {
  if (raw === undefined || !/^\d+$/.test(raw)) return undefined;
  const id = Number(raw);
  return Number.isSafeInteger(id) ? id : undefined;
}

export function ReportPage() {
  const id = parseId(useParams().id);
  if (id === undefined) return <ReportNotFound />;
  return <ReportView id={id} />;
}

function ReportView({ id }: { id: number }) {
  const report = useReport(id);

  if (report.isPending) return <ReportSkeleton />;
  if (report.isError) {
    if (report.error instanceof ApiError && report.error.status === 404) return <ReportNotFound />;
    return (
      <section className="max-w-xl space-y-2 pt-16">
        <h1 className="text-24 font-semibold text-text">Can't load this report.</h1>
        <p className="text-13 text-muted">
          {report.error instanceof ApiError ? report.error.detail : report.error.message}
        </p>
      </section>
    );
  }
  // key: a different snapshot is a fresh load, with its own count-up.
  return <ReportBody key={id} report={report.data} />;
}

const STATUS: Record<string, { text: string; className: string } | undefined> = {
  ok: { text: "Complete", className: "text-muted" },
  done: { text: "Complete", className: "text-muted" },
  capped: { text: "Capped", className: "text-muted" },
  queued: { text: "Queued", className: "text-muted" },
  running: { text: "Running", className: "text-signal" },
  cancelled: { text: "Cancelled", className: "text-muted" },
  blocked: { text: "Blocked", className: "text-warn" },
  error: { text: "Failed", className: "text-fail" },
};

function ReportBody({ report }: { report: Report }) {
  const { snapshot, score } = report;
  const zeroAds = snapshot.ad_count === 0;
  const searchAgain = useSearchAgain(report);
  const demo = useDemo();
  const [tab, setTab] = useState<Tab>("creatives");

  const showExcluded = () => {
    setTab("excluded");
    const tabs = document.getElementById("report-tabs");
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? true;
    tabs?.scrollIntoView?.({ behavior: still ? "auto" : "smooth", block: "start" });
  };

  const searchAgainButton = (
    <DemoLock locked={demo}>
      <Button variant="outline" onClick={searchAgain.run} disabled={searchAgain.pending || demo}>
        Search again
      </Button>
    </DemoLock>
  );

  return (
    <div className="space-y-5">
      <ReportHeader report={report}>
        <WatchButton snapshot={snapshot} />
        {zeroAds ? null : (
          <>
            {searchAgainButton}
            <ExportMenu snapshotId={snapshot.id} />
          </>
        )}
      </ReportHeader>

      {zeroAds ? (
        <Notice text={zeroAdsNotice(report)} detail={zeroAdsDetail(report)}>
          {searchAgainButton}
        </Notice>
      ) : report.notice ? (
        <Notice text={report.notice} tone={snapshot.status === "capped" ? "neutral" : "warn"} />
      ) : null}

      {zeroAds ? null : (
        <>
          <div className="grid border border-rule bg-panel lg:grid-cols-[360px_minmax(0,1fr)]">
            <div className="flex border-b border-rule p-5 md:p-6 lg:border-r lg:border-b-0">
              <ScoreBlock score={score} />
            </div>
            <div className="min-w-0 px-5 pt-4 pb-2 md:px-6">
              <FactorPanel score={score} />
            </div>
          </div>

          <AnglesPanel report={report} onShowExcluded={showExcluded} />

          <LaunchTimeline
            month={report.timeline_month}
            week={report.timeline_week}
            leaderName={score.leader?.page_name ?? null}
          />

          <ReportTabs report={report} tab={tab} onTabChange={setTab} />
        </>
      )}
    </div>
  );
}

function ReportHeader({ report, children }: { report: Report; children: React.ReactNode }) {
  const timezone = useTimezone();
  const { snapshot } = report;
  const status = STATUS[snapshot.status] ?? { text: snapshot.status, className: "text-muted" };

  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-5 gap-y-1">
        <h1 className="min-w-0 truncate text-24 font-semibold tracking-[-0.01em] text-text">
          {snapshot.query}
        </h1>
        <p className="flex flex-wrap items-baseline gap-x-5 text-13 text-muted">
          <span className="text-text">{snapshot.country}</span>
          <time className="num text-12" dateTime={snapshot.started_at}>
            {formatDateTime(snapshot.started_at, timezone)}
          </time>
          <span className={status.className}>{status.text}</span>
        </p>
      </div>
      {children ? <div className="flex shrink-0 items-center gap-2">{children}</div> : null}
    </header>
  );
}

function useSearchAgain(report: Report) {
  const navigate = useNavigate();
  const settings = useSettings();
  const start = useStartSearch();
  const { snapshot } = report;

  const run = () => {
    const query: SearchQuery = {
      kind: snapshot.kind,
      page_id: snapshot.page_id,
      keyword: snapshot.query,
      country: snapshot.country,
      active_status: snapshot.active_status === "all" ? "all" : "active",
      media_type: isMediaType(snapshot.media_type) ? snapshot.media_type : "all",
      limit: Math.min(settings.data?.max_results ?? 300, 1000),
    };
    start.mutate(query, {
      // Home re-attaches to the running search and shows its progress.
      onSuccess: () => void navigate("/"),
      onError: (error) => toast.error(error instanceof ApiError ? error.detail : error.message),
    });
  };

  return { run, pending: start.isPending };
}

/** The backend's notice, or the CLI's wording when a clean run found nothing. */
function zeroAdsNotice({ notice, snapshot }: Report): string {
  return (
    notice ?? `No ads found for "${snapshot.query}" in ${snapshot.country}. Try a broader keyword.`
  );
}

/** Why a blocked or failed run stopped, with any CLI command swapped for app copy. */
function zeroAdsDetail({ snapshot }: Report): string | undefined {
  const message = snapshot.error_message?.trim();
  return message ? forTheApp(message) : undefined;
}

function isMediaType(value: string): value is SearchQuery["media_type"] {
  return value === "all" || value === "image" || value === "video" || value === "meme";
}

function ReportNotFound() {
  return (
    <section className="max-w-xl space-y-4 pt-16">
      <h1 className="text-24 font-semibold text-text">This report doesn't exist.</h1>
      <Link to="/" className={buttonVariants({ variant: "outline" })}>
        Start a new search
      </Link>
    </section>
  );
}

function ReportSkeleton() {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="Loading report">
      <Skeleton className="h-7 w-72" />
      <div className="grid border border-rule bg-panel lg:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-3 p-6">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-16 w-40" />
          <Skeleton className="h-4 w-48" />
        </div>
        <div className="space-y-6 p-6">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className={cn("h-10 w-full")} />
          ))}
        </div>
      </div>
      <Skeleton className="h-64 w-full" />
    </div>
  );
}
