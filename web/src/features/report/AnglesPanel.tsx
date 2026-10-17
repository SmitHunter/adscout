import { useCallback, useId, useState } from "react";
import { Link } from "react-router";

import type { AnalysisJob, AnalysisView, Report, Schemas } from "@/api/client";
import { useSettings, useTimezone } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatDate, formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  backendLabel,
  barWidth,
  EMOTION_LABEL,
  exampleCopies,
  groupOffers,
  isScored,
  modelLabel,
  OFFER_LABEL,
  offTopicCreatives,
  SEASONALITY,
  sortAngles,
  UNAVAILABLE_COPY,
} from "./analysis";
import { AnalyzeButton, AnalyzeConfirm } from "./AnalyzeButton";
import { useAnalysis } from "./useAnalysis";

type AngleView = Schemas["AnalysisAngleView"];
type AdView = Schemas["AdView"];

const CHIP = "inline-block shrink-0 rounded-sm border px-1.5 py-px text-12 font-medium";

function Num({ value }: { value: number }) {
  return <span className="num">{formatInt(value)}</span>;
}

function plural(n: number, one: string, many: string) {
  return n === 1 ? one : many;
}

/**
 * Claude's read of the ad copy: the verdict, angle clusters with live counts,
 * hooks, offers and seasonality. Before a run it explains what analysis adds
 * and offers `Analyze angles`; while a job runs it says so and the hook polls.
 * `onShowExcluded` opens the Excluded tab, where Claude's off-topic ads wait.
 */
export function AnglesPanel({
  report,
  onShowExcluded,
}: {
  report: Report;
  onShowExcluded?: () => void;
}) {
  const settings = useSettings().data ?? undefined;
  // `Try again` (strip or toast) asks through the same dialog as Analyze.
  const [retry, setRetry] = useState({ open: false, force: false });
  const askRetry = useCallback((force: boolean) => setRetry({ open: true, force }), []);
  const analysis = useAnalysis(report, askRetry);
  const data = report.analysis;
  const job = analysis.job;
  const busy = analysis.running || analysis.starting;

  return (
    <section aria-labelledby="angles-heading" className="border border-rule bg-panel">
      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-5 py-3 md:px-6">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-5 gap-y-1">
          <h2 id="angles-heading" className="text-15 font-medium text-text">
            Angles
          </h2>
          {data ? <AnalysisMeta data={data} /> : null}
        </div>
        {data ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <OffTopic report={report} count={data.flagged_off_topic} onShow={onShowExcluded} />
            <AnalyzeButton
              report={report}
              settings={settings}
              rerun
              busy={busy}
              onStart={analysis.start}
              variant="outline"
              size="sm"
            />
          </div>
        ) : null}
      </header>

      {analysis.running && job ? <Running job={job} /> : null}
      {job?.state === "error" && !busy ? (
        <Failed message={job.message} onRetry={() => askRetry(analysis.force)} />
      ) : null}
      <AnalyzeConfirm
        open={retry.open}
        onOpenChange={(open) => setRetry((r) => ({ ...r, open }))}
        report={report}
        settings={settings}
        rerun={retry.force}
        onConfirm={analysis.start}
      />

      {data ? (
        <AnalysisBody data={data} ads={report.ads} />
      ) : analysis.running ? null : (
        <EmptyBody>
          <AnalyzeButton
            report={report}
            settings={settings}
            rerun={false}
            busy={busy}
            onStart={analysis.start}
          />
        </EmptyBody>
      )}
    </section>
  );
}

function AnalysisMeta({ data }: { data: AnalysisView }) {
  const timezone = useTimezone();
  return (
    <p className="flex flex-wrap items-baseline gap-x-4 text-13 text-muted">
      <span>{modelLabel(data.model)}</span>
      <span>{backendLabel(data.backend)}</span>
      <time className="num text-12" dateTime={data.created_at}>
        {formatDate(data.created_at, timezone)}
      </time>
      <span>
        <Num value={data.ad_count} /> {plural(data.ad_count, "creative", "creatives")} read
      </span>
    </p>
  );
}

/**
 * `{n} ads marked off-topic`: Claude's not-the-product creatives, already out
 * of the score. Opens the Excluded tab, where any it got wrong can be included.
 */
function OffTopic({
  report,
  count,
  onShow,
}: {
  report: Report;
  count: number;
  onShow: (() => void) | undefined;
}) {
  if (count === 0) return null;
  const creatives = offTopicCreatives(report.ads);
  const label = `${formatInt(count)} ${plural(count, "ad", "ads")} marked off-topic`;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={onShow}
          className="cursor-pointer rounded-sm px-1 text-13 text-warn underline decoration-warn/40 underline-offset-4 hover:decoration-warn"
        >
          {label}
        </button>
      </TooltipTrigger>
      <TooltipContent className="text-pretty">
        Claude flagged {formatInt(creatives)} {plural(creatives, "creative", "creatives")} (
        {formatInt(count)} {plural(count, "ad", "ads")}) as not selling this product.{" "}
        {plural(count, "It's", "They're")} excluded from the score. Include any it got wrong from
        the Excluded tab.
      </TooltipContent>
    </Tooltip>
  );
}

function Running({ job }: { job: AnalysisJob }) {
  return (
    <div>
      {/* The 2px track is the divider: it scans while Claude works. */}
      <div className="relative h-0.5 overflow-hidden bg-rule" aria-hidden="true">
        <div className="progress-scan absolute inset-y-0 left-0 w-1/3 bg-signal motion-reduce:w-full" />
      </div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 px-5 py-4 md:px-6">
        <p data-testid="analysis-running" role="status" className="text-15 text-text">
          Analyzing <Num value={job.creatives} /> creatives with {modelLabel(job.model)}…
        </p>
        <p className="text-13 text-muted">
          Results appear here when Claude finishes. You can leave this page.
        </p>
      </div>
    </div>
  );
}

function Failed({ message, onRetry }: { message: string | null | undefined; onRetry: () => void }) {
  return (
    <div className="flex flex-col gap-3 border-t border-rule border-l-2 border-l-fail py-2.5 pr-5 pl-[18px] sm:flex-row sm:items-center sm:justify-between md:pr-6 md:pl-[22px]">
      <div className="space-y-0.5">
        <p className="text-13 text-text">The last analysis didn't finish.</p>
        <p className="text-13 text-muted">{message ?? "Angle analysis failed."}</p>
      </div>
      <Button variant="outline" size="sm" className="shrink-0" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}

function EmptyBody({ children }: { children: React.ReactNode }) {
  const settings = useSettings().data;
  const unavailable = settings !== undefined && settings !== null && !settings.analysis.available;

  return (
    <div className="flex flex-col gap-5 border-t border-rule px-5 py-5 md:px-6 lg:flex-row lg:items-center lg:justify-between">
      <div className="max-w-[72ch] space-y-1.5">
        <p className="text-15 text-text">See which angles this product sells on.</p>
        <p className="text-13 text-pretty text-muted">
          Claude reads the relevant ad copy and groups it into angles, opening hooks and offers. It
          flags ads that aren&apos;t selling this product, calls the seasonality and writes a
          three-sentence verdict. The angle count completes the Winner Score.
        </p>
        {unavailable ? (
          <p className="pt-1 text-13 text-warn">
            {settings.analysis.reason ?? UNAVAILABLE_COPY}{" "}
            <Link to="/settings" className="text-signal underline-offset-4 hover:underline">
              Open settings
            </Link>
          </p>
        ) : null}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function AnalysisBody({ data, ads }: { data: AnalysisView; ads: AdView[] }) {
  const { result } = data;
  const angles = sortAngles(data.angles);
  const max = angles.reduce((m, a) => Math.max(m, a.creatives), 0);
  const scored = angles.filter(isScored).length;
  const offers = groupOffers(result.offers);
  const season = SEASONALITY[result.seasonality];

  return (
    <>
      <div className="border-t border-rule px-5 py-5 md:px-6">
        <p
          data-testid="analysis-verdict"
          className="max-w-[78ch] text-18 font-normal text-pretty text-text"
        >
          {result.verdict}
        </p>
      </div>

      <div className="grid border-t border-rule lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 px-5 pt-4 pb-2 md:px-6">
          <SubHeading
            title="Angle clusters"
            aside={
              <>
                <Num value={scored} /> of <Num value={angles.length} /> scored
              </>
            }
          />
          <ul aria-label="Angle clusters" className="mt-1 divide-y divide-rule">
            {angles.map((angle) => (
              <AngleCluster key={angle.name} angle={angle} max={max} ads={ads} />
            ))}
          </ul>
        </div>

        <aside className="space-y-6 border-t border-rule px-5 py-4 md:px-6 lg:border-t-0 lg:border-l">
          <div>
            <SubHeading title="Seasonality" />
            <p className="mt-2 flex items-start gap-3">
              <span className={cn(CHIP, season.className)}>{season.label}</span>
              <span className="text-13 text-pretty text-muted">{result.seasonality_note}</span>
            </p>
          </div>

          <div>
            <SubHeading title="Hooks" aside={<Num value={result.hooks.length} />} />
            {result.hooks.length > 0 ? (
              <ul className="mt-2 space-y-2">
                {result.hooks.map((hook) => (
                  <li key={hook.text} className="text-13 text-pretty text-text">
                    {`“${unquote(hook.text)}”`}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-13 text-muted">No repeated opening lines.</p>
            )}
          </div>

          <div>
            <SubHeading title="Offers" aside={<Num value={result.offers.length} />} />
            {offers.length > 0 ? (
              <div className="mt-2 space-y-3">
                {offers.map((group) => (
                  <OfferGroup key={group.kind} kind={group.kind} texts={group.texts} />
                ))}
              </div>
            ) : (
              <p className="mt-2 text-13 text-muted">No offers in the copy.</p>
            )}
          </div>
        </aside>
      </div>
    </>
  );
}

function SubHeading({ title, aside }: { title: string; aside?: React.ReactNode }) {
  return (
    <div className="flex h-[26px] items-center justify-between gap-4">
      <h3 className="text-13 font-medium text-text">{title}</h3>
      {aside ? <span className="text-12 text-muted">{aside}</span> : null}
    </div>
  );
}

function OfferGroup({ kind, texts }: { kind: Schemas["Offer"]["kind"]; texts: string[] }) {
  const id = useId();
  return (
    <div className="grid grid-cols-[88px_minmax(0,1fr)] items-baseline gap-3">
      <span id={id} className="text-12 text-muted">
        {OFFER_LABEL[kind]}
      </span>
      {/* Plain text, not chips: offer copy runs long and a wrapped chip reads as a text box. */}
      <ul aria-labelledby={id} className="space-y-1">
        {texts.map((text) => (
          <li key={text} className="text-13 text-pretty text-text">
            {text}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AngleCluster({ angle, max, ads }: { angle: AngleView; max: number; ads: AdView[] }) {
  const [open, setOpen] = useState(false);
  const examplesId = useId();
  const scored = isScored(angle);
  const examples = exampleCopies(angle.ad_ids, ads);

  return (
    <li data-testid="angle-cluster" className="py-3.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h4 className={cn("text-13 font-medium", scored ? "text-text" : "text-muted")}>
          {angle.name}
        </h4>
        <span className={cn(CHIP, "border-rule font-normal text-muted")}>
          {EMOTION_LABEL[angle.emotion]}
        </span>
        {scored ? null : (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                tabIndex={0}
                className="rounded-sm text-12 text-muted underline decoration-dotted underline-offset-4"
              >
                Not scored
              </span>
            </TooltipTrigger>
            <TooltipContent>
              Fewer than 2 relevant creatives use this angle, so it doesn&apos;t count toward the
              score.
            </TooltipContent>
          </Tooltip>
        )}
        <span data-testid="angle-count" className="ml-auto text-12 text-muted">
          <Num value={angle.creatives} /> {plural(angle.creatives, "creative", "creatives")},{" "}
          <Num value={angle.ads} /> {plural(angle.ads, "ad", "ads")}
        </span>
      </div>
      <p className="mt-1 max-w-[80ch] text-13 text-pretty text-muted">{angle.description}</p>

      <div className="mt-2 flex items-center gap-4">
        <div className="h-1 min-w-0 flex-1 bg-rule" aria-hidden="true">
          <div
            data-testid="angle-bar"
            className={cn("h-full", scored ? "bg-signal" : "bg-muted")}
            style={{ width: barWidth(angle.creatives, max) }}
          />
        </div>
        {examples.length > 0 ? (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={examplesId}
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 cursor-pointer rounded-sm text-12 text-muted underline-offset-4 hover:text-text hover:underline"
          >
            {open ? "Hide examples" : "Show examples"}
          </button>
        ) : null}
      </div>

      {open ? (
        <ul id={examplesId} className="mt-3 space-y-2">
          {examples.map((example) => (
            <li key={example.id} className="border-l-2 border-rule pl-3">
              <p className="line-clamp-3 text-13 text-pretty text-text">{example.body}</p>
              <p className="mt-0.5 text-12 text-muted">{example.advertiser}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** Claude sometimes quotes the hook itself; the list adds its own quotes. */
function unquote(text: string): string {
  return text.trim().replace(/^["“]+|["”]+$/g, "");
}
