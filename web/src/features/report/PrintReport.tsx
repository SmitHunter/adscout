import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { ImageOff } from "lucide-react";
import { useParams } from "react-router";

import { ApiError, type Report } from "@/api/client";
import { useReport, useTimezone } from "@/api/hooks";
import { PRINT_ROUTE_CLASS } from "@/app/theme";
import { formatDate, formatDateTime, formatInt } from "@/lib/format";

import { FactorRuler } from "./FactorRuler";
import {
  advertiserName,
  groupCreatives,
  imageSources,
  sortGroups,
  type AdvertiserRow,
  type CreativeGroup,
} from "./gallery";
import { MiniTimeline } from "./MiniTimeline";
import { ScoreBlock } from "./ScoreBlock";

/** The longest an export waits on creatives before printing what it has. */
export const IMAGE_WAIT_MS = 10_000;
const TOP_CREATIVES = 6;
const TOP_ADVERTISERS = 5;

function parseId(raw: string | undefined): number | undefined {
  if (raw === undefined || !/^\d+$/.test(raw)) return undefined;
  const id = Number(raw);
  return Number.isSafeInteger(id) ? id : undefined;
}

/**
 * Light theme while the print page is open, whatever the user picked. Only the
 * attribute changes: the stored choice is untouched and comes back on leaving.
 */
function useForcedLightTheme() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    const previous = root.getAttribute("data-theme");
    root.setAttribute("data-theme", "light");
    root.classList.add(PRINT_ROUTE_CLASS);
    return () => {
      root.classList.remove(PRINT_ROUTE_CLASS);
      if (previous === null) root.removeAttribute("data-theme");
      else root.setAttribute("data-theme", previous);
    };
  }, []);
}

/** `<body data-print-ready>` while `ready`: the exporter waits for it before printing. */
function usePrintReady(ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    document.body.setAttribute("data-print-ready", "");
    return () => document.body.removeAttribute("data-print-ready");
  }, [ready]);
}

export type PrintFailure = "not-found" | "load";

/**
 * `<body data-print-failed>` when the report can't be shown, so the exporter
 * stops at once with the reason instead of waiting out its timeout.
 */
function usePrintFailed(failure: PrintFailure | null) {
  useEffect(() => {
    if (failure === null) return;
    document.body.setAttribute("data-print-failed", failure);
    return () => document.body.removeAttribute("data-print-failed");
  }, [failure]);
}

/**
 * `/report/:id/print`: the report on one A4 page, for the PDF and PNG exports.
 * No rail, no controls, no motion; light whatever the theme.
 */
export function PrintReport() {
  useForcedLightTheme();
  const id = parseId(useParams().id);
  const report = useReport(id);

  const failure: PrintFailure | null =
    id === undefined ||
    (report.isError && report.error instanceof ApiError && report.error.status === 404)
      ? "not-found"
      : report.isError
        ? "load"
        : null;
  usePrintFailed(failure);

  let body: React.ReactNode;
  if (failure === "not-found") body = <PrintMessage text="This report doesn't exist." />;
  else if (failure === "load") body = <PrintMessage text="Can't load this report." />;
  else if (report.data === undefined) body = <PrintMessage text="Loading report." />;
  else body = <PrintSheet report={report.data} />;

  return (
    <div data-still className="print-root">
      {body}
    </div>
  );
}

function PrintMessage({ text }: { text: string }) {
  return (
    <article className="print-sheet">
      <p className="text-15 text-muted">{text}</p>
    </article>
  );
}

function topCreatives(report: Report): CreativeGroup[] {
  const relevant = report.ads.filter((ad) => ad.relevant);
  return sortGroups(groupCreatives(relevant), "longest").slice(0, TOP_CREATIVES);
}

function topAdvertisers(report: Report): AdvertiserRow[] {
  // The API sends them most creatives first; only those with relevant ads count.
  return report.advertisers.filter((row) => !row.excluded && row.ads > 0).slice(0, TOP_ADVERTISERS);
}

/** 190 mm and 276 mm (a hair under the 277 mm page box) in CSS px. */
const CONTENT_WIDTH_PX = (190 * 96) / 25.4;
const PAGE_HEIGHT_PX = (276 * 96) / 25.4;
/** The footer's minimum gap above it. */
const FOOTER_GAP_PX = 16;

/**
 * The print zoom that keeps the sheet on one A4 page: 1 when it fits, less
 * when an unusually long verdict or factor note pushes it over. Measured as a
 * ratio to the content width, so the screen zoom (PNG export) doesn't matter.
 */
function pageFit(content: HTMLElement | null, footer: HTMLElement | null): number {
  if (content === null || footer === null) return 1;
  const box = content.getBoundingClientRect();
  if (box.width === 0) return 1;
  const scale = CONTENT_WIDTH_PX / box.width;
  const needed = (box.height + footer.getBoundingClientRect().height) * scale + FOOTER_GAP_PX;
  return needed <= PAGE_HEIGHT_PX ? 1 : Math.floor((PAGE_HEIGHT_PX / needed) * 100) / 100;
}

/** Fonts settle a frame after first paint; jsdom has no font loading at all. */
function useFontsReady(): boolean {
  const [ready, setReady] = useState(() => typeof document.fonts === "undefined");
  useEffect(() => {
    if (ready) return;
    let live = true;
    const frame = requestAnimationFrame(() => {
      void document.fonts.ready.then(() => {
        if (live) setReady(true);
      });
    });
    return () => {
      live = false;
      cancelAnimationFrame(frame);
    };
  }, [ready]);
  return ready;
}

function PrintSheet({ report }: { report: Report }) {
  const timezone = useTimezone();
  const { snapshot, score } = report;
  const creatives = useMemo(() => topCreatives(report), [report]);
  const advertisers = useMemo(() => topAdvertisers(report), [report]);
  const verdict = report.analysis?.result.verdict.trim() || null;
  const leader = score.leader;
  const factors = leader !== null ? leader.factors : score.factors;

  const [settled, setSettled] = useState<ReadonlySet<string>>(() => new Set());
  const [timedOut, setTimedOut] = useState(false);
  const fontsReady = useFontsReady();
  const onSettled = useCallback(
    (key: string) => setSettled((prev) => (prev.has(key) ? prev : new Set(prev).add(key))),
    [],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => setTimedOut(true), IMAGE_WAIT_MS);
    return () => window.clearTimeout(timer);
  }, []);

  const imagesDone = creatives.every((group) => settled.has(group.key));
  const contentRef = useRef<HTMLDivElement>(null);
  const footerRef = useRef<HTMLElement>(null);
  const [fit, setFit] = useState<number | null>(null);

  // Once fonts have settled the wrapping, check the sheet fits one page.
  useLayoutEffect(() => {
    if (!fontsReady) return;
    setFit(pageFit(contentRef.current, footerRef.current));
  }, [fontsReady, report]);

  usePrintReady(fit !== null && (imagesDone || timedOut));

  const headingIds = { advertisers: useId(), creatives: useId(), timeline: useId() };

  return (
    <article
      className="print-sheet"
      data-fit={fit ?? undefined}
      style={{ "--print-fit": fit ?? 1 } as CSSProperties}
    >
      <div ref={contentRef}>
        <header className="flex items-end justify-between gap-6 border-b-2 border-text pb-3">
          <div className="min-w-0">
            <h1 className="truncate text-24 font-semibold tracking-[-0.01em]">{snapshot.query}</h1>
            <p className="mt-1 flex flex-wrap items-baseline gap-x-5 text-13 text-muted">
              <span className="text-text">{snapshot.country}</span>
              <time className="num text-12" dateTime={snapshot.started_at}>
                {formatDateTime(snapshot.started_at, timezone)}
              </time>
              <span>
                <span className="num text-12">{formatInt(snapshot.ad_count)}</span> ads
              </span>
              <span>
                <span className="num text-12">{formatInt(snapshot.advertiser_count)}</span>{" "}
                {snapshot.advertiser_count === 1 ? "advertiser" : "advertisers"}
              </span>
              {snapshot.status === "capped" ? <span>Capped at the result limit</span> : null}
            </p>
          </div>
          <span className="shrink-0 text-18 leading-none font-bold tracking-[-0.01em] font-dense">
            AdScout
          </span>
        </header>

        <section className="mt-4 grid grid-cols-[248px_minmax(0,1fr)] border border-rule">
          <div className="flex border-r border-rule p-4">
            <ScoreBlock score={score} animate={false} warnings={false} />
          </div>
          <div className="min-w-0 px-4 pt-3 pb-1">
            <h2 className="text-12 text-muted">
              {leader !== null ? `Factors for ${leader.page_name}` : "Market factors"}
            </h2>
            <ul className="print-factors divide-y divide-rule">
              {factors.map((factor) => (
                <FactorRuler key={factor.key} factor={factor} />
              ))}
            </ul>
          </div>
        </section>

        {verdict !== null ? (
          <section className="mt-4 border-l-2 border-signal pl-4" aria-label="Verdict">
            <p data-testid="analysis-verdict" className="text-13 leading-[19px] text-pretty">
              {verdict}
            </p>
          </section>
        ) : null}

        {creatives.length > 0 ? (
          <section className="mt-4" aria-labelledby={headingIds.creatives}>
            <h2 id={headingIds.creatives} className="text-13 font-medium">
              Longest-running creatives
            </h2>
            <ul className="mt-2 grid grid-cols-6 gap-2">
              {creatives.map((group) => (
                <PrintCreative
                  key={group.key}
                  group={group}
                  timezone={timezone}
                  onSettled={onSettled}
                />
              ))}
            </ul>
          </section>
        ) : null}

        <div className="mt-4 grid grid-cols-[minmax(0,1fr)_292px] gap-4">
          <section className="min-w-0 border border-rule p-3" aria-labelledby={headingIds.timeline}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h2 id={headingIds.timeline} className="text-13 font-medium">
                New creatives per month
              </h2>
              <p className="flex items-center gap-3 text-12 text-muted">
                {leader !== null ? (
                  <span className="flex items-center gap-1.5">
                    <span className="size-2 rounded-[1px] bg-signal" aria-hidden="true" />
                    <span className="max-w-28 truncate">{leader.page_name}</span>
                  </span>
                ) : null}
                <span className="flex items-center gap-1.5">
                  <span className="size-2 rounded-[1px] bg-muted/75" aria-hidden="true" />
                  {leader !== null ? "Others" : "All advertisers"}
                </span>
              </p>
            </div>
            <MiniTimeline buckets={report.timeline_month} height={104} className="mt-3" />
          </section>

          <section className="min-w-0" aria-labelledby={headingIds.advertisers}>
            <h2 id={headingIds.advertisers} className="text-13 font-medium">
              Top advertisers
            </h2>
            {advertisers.length > 0 ? (
              <table
                aria-labelledby={headingIds.advertisers}
                className="mt-1.5 w-full table-fixed font-dense text-12"
              >
                <thead>
                  <tr className="border-b border-rule text-muted">
                    <th scope="col" className="py-1 text-left font-normal">
                      Advertiser
                    </th>
                    <th scope="col" className="w-16 py-1 text-right font-normal">
                      Creatives
                    </th>
                    <th scope="col" className="w-10 py-1 text-right font-normal">
                      Ads
                    </th>
                    <th scope="col" className="w-16 py-1 text-right font-normal">
                      Oldest ad
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-rule">
                  {advertisers.map((row) => (
                    <tr key={row.key}>
                      <th scope="row" className="truncate py-1 pr-2 text-left font-medium">
                        {row.is_leader ? (
                          <span
                            className="mr-1.5 inline-block size-1.5 rounded-full bg-signal align-middle"
                            aria-label="Leader"
                          />
                        ) : null}
                        {row.name}
                      </th>
                      <td className="num py-1 text-right">{formatInt(row.creatives)}</td>
                      <td className="num py-1 text-right">{formatInt(row.ads)}</td>
                      <td className="py-1 text-right text-muted">
                        <span className="num text-text">{formatInt(row.days_running_max)}</span> d
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="mt-1.5 text-12 text-muted">No advertisers in this snapshot.</p>
            )}
          </section>
        </div>
      </div>

      <footer
        ref={footerRef}
        className="mt-auto flex items-baseline justify-between gap-4 border-t border-rule pt-2 text-12 text-muted"
      >
        <span>Generated by AdScout</span>
        <span className="flex gap-x-4">
          <span className="text-text">{snapshot.query}</span>
          <span>{snapshot.country}</span>
          <time className="num" dateTime={snapshot.started_at}>
            {formatDate(snapshot.started_at, timezone)}
          </time>
        </span>
      </footer>
    </article>
  );
}

/**
 * One creative: its image (the saved file, then the CDN), days running and the
 * advertiser. Reports `onSettled` once the image has loaded or every source failed.
 */
function PrintCreative({
  group,
  timezone,
  onSettled,
}: {
  group: CreativeGroup;
  timezone: string;
  onSettled: (key: string) => void;
}) {
  const { lead } = group;
  const item = lead.media[0];
  const sources = imageSources(item);
  const [failed, setFailed] = useState(0);
  const src = sources[failed];
  const name = advertiserName(lead);

  useEffect(() => {
    if (src === undefined) onSettled(group.key);
  }, [src, group.key, onSettled]);

  return (
    <li data-testid="print-creative" className="flex min-w-0 flex-col border border-rule">
      <div className="relative aspect-square overflow-hidden bg-bg">
        {src !== undefined ? (
          <img
            key={src}
            src={src}
            alt={`Ad creative from ${name}`}
            loading="eager"
            decoding="sync"
            referrerPolicy="no-referrer"
            onLoad={() => onSettled(group.key)}
            onError={() => setFailed((n) => n + 1)}
            className="size-full object-cover"
          />
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-1 text-muted">
            <ImageOff className="size-4" aria-hidden="true" />
            <span className="text-12">Media unavailable</span>
          </div>
        )}
        {item?.type === "video" ? (
          <span className="absolute top-1 right-1 bg-panel/90 px-1 text-12 text-muted">Video</span>
        ) : null}
      </div>
      <div className="px-1.5 pt-1 pb-1.5 font-dense text-12">
        <p title={`Running since ${formatDate(lead.start_date, timezone)}`}>
          <span className="num font-medium">{formatInt(lead.days_running)}</span>{" "}
          <span className="text-muted">{lead.days_running === 1 ? "day" : "days"}</span>
        </p>
        <p className="truncate text-muted" title={name}>
          {name}
        </p>
      </div>
    </li>
  );
}
