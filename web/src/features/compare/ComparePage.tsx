import { useId, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { ColumnsIcon, XIcon } from "lucide-react";
import { Link, useSearchParams } from "react-router";

import { ApiError, type CompareColumn, type Snapshot } from "@/api/client";
import { useCompare, useSnapshots, useTimezone } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { FactorRuler } from "@/features/report/FactorRuler";
import { MiniTimeline } from "@/features/report/MiniTimeline";
import { scoreBand } from "@/features/report/ruler";
import { formatDateTime, formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  factorRows,
  MAX_COMPARE,
  parseIds,
  sharedTimelineMax,
  sharedTimelines,
  type FactorRow,
  type FactorView,
} from "./compare";

/** Months of launches each column's timeline shows. */
const TIMELINE_MONTHS = 18;

const BAND_TEXT = { pass: "text-pass", warn: "text-warn", fail: "text-fail" } as const;

/** Searches that finished with ads can be compared. */
const COMPARABLE = new Set(["ok", "done", "capped"]);

export function ComparePage() {
  const [params, setParams] = useSearchParams();
  const ids = useMemo(() => parseIds(params.get("ids")), [params]);
  const [view, setView] = useState<FactorView>("leader");

  const setIds = (next: number[]) =>
    setParams(next.length > 0 ? { ids: next.join(",") } : {}, { replace: true });

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="space-y-1">
          <h1 className="text-24 font-semibold tracking-[-0.01em] text-text">Compare</h1>
          <p className="text-13 text-muted">Up to four searches side by side, factor by factor.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {ids.length >= 2 ? (
            <ToggleGroup
              type="single"
              size="sm"
              value={view}
              onValueChange={(next) => {
                if (next === "leader" || next === "market") setView(next);
              }}
              aria-label="Show factors for"
            >
              <ToggleGroupItem value="leader">Leading advertiser</ToggleGroupItem>
              <ToggleGroupItem value="market">Market</ToggleGroupItem>
            </ToggleGroup>
          ) : null}
          <ComparePicker selected={ids} onChange={setIds} />
        </div>
      </header>

      {ids.length < 2 ? (
        <div className="border border-rule bg-panel px-6 py-10">
          <ColumnsIcon className="size-5 text-muted" aria-hidden="true" />
          <p className="mt-3 text-15 font-medium text-text">
            Pick two to four searches to compare them side by side.
          </p>
          <p className="mt-1 max-w-[60ch] text-13 text-muted">
            Winner Scores, factors, launches and verdicts line up row by row, with the best value in
            each factor outlined.
          </p>
        </div>
      ) : (
        <CompareResults
          ids={ids}
          view={view}
          onRemove={(id) => setIds(ids.filter((i) => i !== id))}
        />
      )}
    </div>
  );
}

function CompareResults({
  ids,
  view,
  onRemove,
}: {
  ids: number[];
  view: FactorView;
  onRemove: (id: number) => void;
}) {
  const compare = useCompare(ids);

  if (compare.isPending) return <CompareSkeleton count={ids.length} />;
  if (compare.isError) {
    return (
      <div className="border border-rule bg-panel px-6 py-8">
        <p className="text-15 font-medium text-text">Can&apos;t compare these searches.</p>
        <p className="mt-1 text-13 text-muted">
          {compare.error instanceof ApiError ? compare.error.detail : compare.error.message}
        </p>
      </div>
    );
  }
  return <CompareGrid columns={compare.data} view={view} onRemove={onRemove} />;
}

/** One band of the grid: a cell per column, every band on the same column tracks. */
function Band({
  count,
  label,
  children,
  className,
  testId,
}: {
  count: number;
  label?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <div className="border-t border-rule first:border-t-0">
      {label ? <div className="px-4 pt-3 text-12 text-muted">{label}</div> : null}
      <div
        data-testid={testId}
        className={cn("grid divide-x divide-rule", className)}
        style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` } as CSSProperties}
      >
        {children}
      </div>
    </div>
  );
}

function CompareGrid({
  columns,
  view,
  onRemove,
}: {
  columns: CompareColumn[];
  view: FactorView;
  onRemove: (id: number) => void;
}) {
  const timezone = useTimezone();
  const rows = factorRows(columns, view);
  const timelines = sharedTimelines(columns, TIMELINE_MONTHS);
  const max = sharedTimelineMax(timelines);
  const count = columns.length;

  return (
    <section aria-label="Compared searches" className="border border-rule bg-panel">
      <Band count={count}>
        {columns.map(({ snapshot }) => (
          <div key={snapshot.id} className="flex min-w-0 items-start gap-2 px-4 pt-4 pb-3">
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-15 font-medium text-text">
                <Link
                  to={`/report/${snapshot.id}`}
                  className="underline-offset-4 hover:underline"
                  title={snapshot.query}
                >
                  {snapshot.query}
                </Link>
              </h2>
              <p className="mt-0.5 flex flex-wrap items-baseline gap-x-3 text-12 text-muted">
                <span className="text-text">{snapshot.country}</span>
                <time className="num" dateTime={snapshot.started_at}>
                  {formatDateTime(snapshot.started_at, timezone)}
                </time>
                <span>
                  <span className="num">{formatInt(snapshot.ad_count)}</span> ads
                </span>
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              className="-mt-1 -mr-2"
              aria-label={`Remove ${snapshot.query} from the comparison`}
              onClick={() => onRemove(snapshot.id)}
            >
              <XIcon aria-hidden="true" />
            </Button>
          </div>
        ))}
      </Band>

      <Band count={count}>
        {columns.map((column) => (
          <CompareScore key={column.snapshot.id} column={column} />
        ))}
      </Band>

      <h3 className="border-t border-rule px-4 pt-3 text-12 text-muted">
        {view === "leader"
          ? "Factors for each leading advertiser, or the market where none leads"
          : "Market factors"}
      </h3>
      {rows.map((row) => (
        <FactorBand key={row.key} row={row} columns={columns} />
      ))}

      <Band
        count={count}
        label={
          <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span>New creatives per month</span>
            <span className="flex items-center gap-1.5">
              <span className="size-2 rounded-[1px] bg-signal" aria-hidden="true" />
              Leading advertiser
            </span>
            <span className="flex items-center gap-1.5">
              <span className="size-2 rounded-[1px] bg-muted/75" aria-hidden="true" />
              Others
            </span>
          </span>
        }
      >
        {columns.map((column, i) => (
          <div key={column.snapshot.id} className="min-w-0 px-4 pt-2 pb-4">
            <MiniTimeline buckets={timelines[i] ?? []} max={max} height={56} />
          </div>
        ))}
      </Band>

      <Band count={count} label="Verdict">
        {columns.map((column) => (
          <div key={column.snapshot.id} className="min-w-0 px-4 pt-1.5 pb-4">
            {column.analysis ? (
              <p className="text-13 text-pretty text-text">{column.analysis.verdict}</p>
            ) : (
              <p className="text-13 text-muted">Not analysed yet.</p>
            )}
          </div>
        ))}
      </Band>
    </section>
  );
}

function FactorBand({ row, columns }: { row: FactorRow; columns: CompareColumn[] }) {
  return (
    <Band count={columns.length} testId={`compare-row-${row.key}`}>
      {row.cells.map((factor, i) => {
        const best = row.best[i] === true;
        const id = columns[i]?.snapshot.id;
        return (
          <div
            key={id}
            data-testid="compare-cell"
            data-snapshot={id}
            data-best={best ? "true" : "false"}
            className={cn(
              "relative min-w-0 px-4",
              best && "bg-signal/6 outline-2 -outline-offset-2 outline-signal",
            )}
          >
            {factor ? (
              <ul>
                <FactorRuler factor={factor} />
              </ul>
            ) : (
              <p className="py-3.5 text-13 text-muted">No data</p>
            )}
            {best ? (
              <p className="-mt-2 pb-2.5 text-right text-12 font-medium text-signal">Best</p>
            ) : null}
          </div>
        );
      })}
    </Band>
  );
}

function CompareScore({ column }: { column: CompareColumn }) {
  const band = scoreBand(column.score);
  const { leader } = column;

  return (
    <div className="flex min-w-0 flex-col px-4 pt-3 pb-4">
      <p className="text-12 text-muted">Winner Score</p>
      <p className="mt-1 flex items-baseline gap-1.5">
        <span
          data-testid="compare-score"
          className={cn("num text-40 font-medium tracking-[-0.05em]", BAND_TEXT[band])}
        >
          {column.score}
        </span>
        <span className="num text-13 text-muted">/100</span>
        {column.provisional ? (
          <span
            className="ml-auto self-center rounded-sm border border-muted/60 px-1.5 py-px text-12 text-muted"
            title="Angle analysis not run yet"
          >
            Provisional
          </span>
        ) : null}
      </p>
      {leader ? (
        <div className="mt-2 min-w-0">
          <p className="truncate text-13 font-medium text-text" title={leader.page_name}>
            {leader.page_name}
          </p>
          <p className="text-12 text-muted">
            <span className="num">{formatInt(leader.ads)}</span> {leader.ads === 1 ? "ad" : "ads"},{" "}
            <span className="num">{formatInt(leader.creatives)}</span>{" "}
            {leader.creatives === 1 ? "creative" : "creatives"}
          </p>
        </div>
      ) : (
        <p className="mt-2 text-12 text-muted">No single advertiser leads. Scored on the market.</p>
      )}
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 border-t border-rule pt-3 text-12">
        <Stat label="Market demand" value={column.market_score} />
        <Stat label="Advertisers" value={column.advertisers} />
        <Stat label="Relevant ads" value={column.relevant_ads} />
        <Stat label="Creatives" value={column.creatives} />
      </dl>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col-reverse gap-0.5">
      <dt className="text-muted">{label}</dt>
      <dd className="num text-15 text-text">{formatInt(value)}</dd>
    </div>
  );
}

/** A checklist of finished searches; at most four can be ticked. */
function ComparePicker({
  selected,
  onChange,
}: {
  selected: number[];
  onChange: (ids: number[]) => void;
}) {
  const snapshots = useSnapshots();
  const timezone = useTimezone();
  const [filter, setFilter] = useState("");
  const filterId = useId();
  const full = selected.length >= MAX_COMPARE;

  const options = (snapshots.data ?? []).filter(
    (s) =>
      (selected.includes(s.id) || (COMPARABLE.has(s.status) && s.ad_count > 0)) &&
      s.query.toLowerCase().includes(filter.trim().toLowerCase()),
  );

  const toggle = (snapshot: Snapshot) => {
    if (selected.includes(snapshot.id)) onChange(selected.filter((id) => id !== snapshot.id));
    else if (!full) onChange([...selected, snapshot.id]);
  };

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline">
          Choose searches
          <span className="text-muted">
            <span className="num">{selected.length}</span> of {MAX_COMPARE}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[360px]" aria-label="Choose searches">
        <div className="space-y-2 border-b border-rule p-3">
          <p className="text-13 text-text">Pick up to {MAX_COMPARE} searches.</p>
          <label htmlFor={filterId} className="sr-only">
            Filter searches
          </label>
          <Input
            id={filterId}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by keyword"
            className="h-8"
          />
        </div>
        {snapshots.isPending ? (
          <div className="space-y-2 p-3" aria-busy="true">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        ) : options.length === 0 ? (
          <p className="p-3 text-13 text-muted">
            {filter ? "No searches match." : "Finished searches will appear here."}
          </p>
        ) : (
          <ul className="max-h-80 overflow-y-auto py-1">
            {options.map((snapshot) => {
              const checked = selected.includes(snapshot.id);
              const disabled = !checked && full;
              return (
                <li key={snapshot.id}>
                  <label
                    className={cn(
                      "flex cursor-pointer items-start gap-3 px-3 py-2 hover:bg-rule/40",
                      disabled && "cursor-not-allowed opacity-50 hover:bg-transparent",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={() => toggle(snapshot)}
                      className="mt-0.5 size-3.5 shrink-0 accent-(--signal)"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-13 font-medium text-text">
                        {snapshot.query}
                      </span>
                      <span className="mt-0.5 flex gap-x-3 text-12 text-muted">
                        <span>{snapshot.country}</span>
                        <time className="num" dateTime={snapshot.started_at}>
                          {formatDateTime(snapshot.started_at, timezone)}
                        </time>
                        <span>
                          <span className="num">{formatInt(snapshot.ad_count)}</span> ads
                        </span>
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}
        {full ? (
          <p className="border-t border-rule px-3 py-2 text-12 text-muted">
            Remove one to add another.
          </p>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function CompareSkeleton({ count }: { count: number }) {
  return (
    <div
      className="grid gap-px border border-rule bg-rule"
      style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }}
      aria-busy="true"
      aria-label="Loading comparison"
    >
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="space-y-4 bg-panel p-4">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-10 w-20" />
          {[0, 1, 2, 3].map((j) => (
            <Skeleton key={j} className="h-12 w-full" />
          ))}
        </div>
      ))}
    </div>
  );
}
