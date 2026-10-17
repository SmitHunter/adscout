import { useState } from "react";
import { ArrowLeftIcon } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";

import { ApiError, type TrendPoint, type WatchAlert, type WatchItem } from "@/api/client";
import {
  useAlerts,
  useMarkAlertsRead,
  useRemoveWatch,
  useRunWatch,
  useSetWatchEnabled,
  useTimezone,
  useWatchTrend,
} from "@/api/hooks";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useDemo } from "@/features/demo/demo";
import { DemoLock } from "@/features/demo/DemoLock";
import {
  formatDateTime,
  formatDayMonth,
  formatInt,
  formatRelative,
  formatWeekdayTime,
} from "@/lib/format";
import { cn } from "@/lib/utils";

import { AlertBody } from "./AlertsMenu";
import { RemoveWatchDialog } from "./RemoveWatchDialog";
import { CAPPED_COPY, HollowKey, TrendCharts } from "./TrendCharts";
import { checkLabel, checkStatus, KIND_LABEL, scopeLabel, TONE_TEXT } from "./watch";
import { ScoreChip } from "./ScoreChip";

function parseId(raw: string | undefined): number | undefined {
  if (raw === undefined || !/^\d+$/.test(raw)) return undefined;
  const id = Number(raw);
  return Number.isSafeInteger(id) ? id : undefined;
}

export function WatchItemPage() {
  const id = parseId(useParams().id);
  if (id === undefined) return <ItemNotFound />;
  return <WatchItemView id={id} />;
}

function WatchItemView({ id }: { id: number }) {
  const trend = useWatchTrend(id);

  if (trend.isPending) {
    return (
      <div className="space-y-5" aria-busy="true" aria-label="Loading trend">
        <Skeleton className="h-7 w-72" />
        <Skeleton className="h-[500px] w-full" />
      </div>
    );
  }
  if (trend.isError) {
    if (trend.error instanceof ApiError && trend.error.status === 404) return <ItemNotFound />;
    return (
      <section className="max-w-xl space-y-2 pt-16">
        <h1 className="text-24 font-semibold text-text">Can&apos;t load this watch item.</h1>
        <p className="text-13 text-muted">
          {trend.error instanceof ApiError ? trend.error.detail : trend.error.message}
        </p>
      </section>
    );
  }
  return <WatchItemBody item={trend.data.item} points={trend.data.points} />;
}

function WatchItemBody({ item, points }: { item: WatchItem; points: TrendPoint[] }) {
  const timezone = useTimezone();
  const navigate = useNavigate();
  const run = useRunWatch();
  const demo = useDemo();
  const setEnabled = useSetWatchEnabled();
  const remove = useRemoveWatch();
  const [confirming, setConfirming] = useState(false);
  const status = checkStatus(item);
  const anyCapped = points.some((p) => p.capped);
  const first = points[0];

  const runNow = () =>
    run.mutate(item.id, {
      onSuccess: (res) => toast.success(res.message || `Checking ${item.query} now.`),
      onError: (err) => toast.error(err instanceof ApiError ? err.detail : err.message),
    });

  return (
    <div className="space-y-5">
      <Link to="/watch" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "-ml-2.5")}>
        <ArrowLeftIcon aria-hidden="true" />
        Watchlist
      </Link>

      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 space-y-1">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-4">
            <h1 className="min-w-0 truncate text-24 font-semibold tracking-[-0.01em] text-text">
              {item.query}
            </h1>
            <span className="text-13 text-muted">{KIND_LABEL[item.kind]}</span>
            <span className="text-13 text-text">{scopeLabel(item.country)}</span>
          </div>
          <p className="flex flex-wrap items-baseline gap-x-5 text-13 text-muted">
            <span>
              Last check{" "}
              {item.last_run_at ? (
                <time
                  dateTime={item.last_run_at}
                  title={formatDateTime(item.last_run_at, timezone)}
                  className="text-text"
                >
                  {formatRelative(item.last_run_at, new Date(), timezone)}
                </time>
              ) : (
                <span className="text-text">never</span>
              )}
            </span>
            <span className={TONE_TEXT[status.tone]}>{status.text}</span>
            {item.enabled && item.next_run_at ? (
              <span>
                Next check{" "}
                <time dateTime={item.next_run_at} className="num text-12 text-text">
                  {formatWeekdayTime(item.next_run_at, timezone)}
                </time>
              </span>
            ) : !item.enabled ? (
              <span>Daily checks paused</span>
            ) : null}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <label className="mr-2 flex items-center gap-2 text-13 text-muted">
            <Switch
              checked={item.enabled}
              onCheckedChange={(enabled) =>
                setEnabled.mutate(
                  { id: item.id, enabled },
                  {
                    onError: (err) =>
                      toast.error(err instanceof ApiError ? err.detail : err.message),
                  },
                )
              }
            />
            Check daily
          </label>
          <DemoLock locked={demo}>
            <Button
              variant="outline"
              disabled={item.running || run.isPending || demo}
              onClick={runNow}
            >
              {item.running ? "Checking" : "Run now"}
            </Button>
          </DemoLock>
          <Button variant="ghost" disabled={item.running} onClick={() => setConfirming(true)}>
            Remove
          </Button>
        </div>
      </header>

      <section aria-labelledby="trend-heading" className="border border-rule bg-panel">
        <header className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-rule px-4 py-3 md:px-5">
          <div className="flex flex-wrap items-baseline gap-x-4">
            <h2 id="trend-heading" className="text-15 font-medium text-text">
              Trend
            </h2>
            {first ? (
              <p className="text-13 text-muted">
                <span className="num text-12">{formatInt(points.length)}</span>{" "}
                {points.length === 1 ? "check" : "checks"} since{" "}
                <span className="num text-12">{formatDayMonth(first.date)}</span>
              </p>
            ) : null}
          </div>
          {anyCapped ? (
            <p className="flex items-center gap-1.5 text-12 text-muted">
              <HollowKey />
              {CAPPED_COPY}, so the count is a floor
            </p>
          ) : null}
        </header>
        <TrendCharts points={points} timezone={timezone} />
      </section>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <ItemAlerts itemId={item.id} timezone={timezone} />
        <Checks points={points} timezone={timezone} />
      </div>

      <RemoveWatchDialog
        query={confirming ? item.query : null}
        onOpenChange={setConfirming}
        onConfirm={() =>
          remove.mutate(item.id, {
            onSuccess: () => {
              toast.success(`Stopped watching ${item.query}.`);
              void navigate("/watch");
            },
            onError: (err) => toast.error(err instanceof ApiError ? err.detail : err.message),
          })
        }
      />
    </div>
  );
}

function ItemAlerts({ itemId, timezone }: { itemId: number; timezone: string }) {
  const alerts = useAlerts();
  const markRead = useMarkAlertsRead();
  const mine: WatchAlert[] = (alerts.data ?? []).filter((a) => a.watch_item_id === itemId);
  const unread = mine.filter((a) => !a.read).map((a) => a.id);

  return (
    <section
      aria-labelledby="item-alerts-heading"
      className="self-start border border-rule bg-panel"
    >
      <header className="flex h-11 items-center justify-between gap-4 border-b border-rule px-4 md:px-5">
        <h2 id="item-alerts-heading" className="text-15 font-medium text-text">
          Alerts
        </h2>
        {unread.length > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            className="-mr-2"
            onClick={() => markRead.mutate({ ids: unread, all: false })}
          >
            Mark read
          </Button>
        ) : null}
      </header>
      {mine.length === 0 ? (
        <p className="px-4 py-5 text-13 text-pretty text-muted md:px-5">
          No alerts yet. AdScout raises one when active ads move 30% or more between checks.
        </p>
      ) : (
        <ul className="max-h-96 divide-y divide-rule overflow-y-auto">
          {mine.map((alert) => (
            <li key={alert.id}>
              <AlertBody alert={alert} timezone={timezone} withQuery={false} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Checks({ points, timezone }: { points: TrendPoint[]; timezone: string }) {
  const newest = points.map((p, i) => ({ p, label: checkLabel(points, i, timezone) })).reverse();
  return (
    <section aria-labelledby="checks-heading" className="border border-rule bg-panel">
      <header className="flex h-11 items-center border-b border-rule px-4 md:px-5">
        <h2 id="checks-heading" className="text-15 font-medium text-text">
          Checks
        </h2>
      </header>
      {newest.length === 0 ? (
        <p className="px-4 py-5 text-13 text-muted md:px-5">No checks yet. Run one now.</p>
      ) : (
        <div className="relative max-h-96 overflow-auto">
          <table className="w-full min-w-[520px] font-dense text-13">
            <caption className="sr-only">Checks, newest first</caption>
            <thead className="sticky top-0 bg-panel">
              <tr className="border-b border-rule text-12 text-muted">
                <th scope="col" className="h-8 pl-4 text-left font-normal md:pl-5">
                  Date
                </th>
                <th scope="col" className="h-8 px-3 text-right font-normal">
                  Active ads
                </th>
                <th scope="col" className="h-8 px-3 text-right font-normal">
                  New creatives
                </th>
                <th scope="col" className="h-8 px-3 text-right font-normal">
                  Advertisers
                </th>
                <th scope="col" className="h-8 px-3 text-center font-normal">
                  Score
                </th>
                <th scope="col" className="h-8 pr-4 md:pr-5">
                  <span className="sr-only">Report</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {newest.map(({ p, label }) => (
                <tr key={p.snapshot_id} className="border-b border-rule last:border-b-0">
                  <th scope="row" className="num py-1.5 pl-4 text-left text-12 font-normal md:pl-5">
                    {label}
                  </th>
                  <td className="num px-3 py-1.5 text-right text-text">
                    {formatInt(p.active_ads)}
                    {p.capped ? (
                      <span className="text-muted" title={CAPPED_COPY}>
                        +<span className="sr-only"> ({CAPPED_COPY.toLowerCase()})</span>
                      </span>
                    ) : null}
                  </td>
                  <td className="num px-3 py-1.5 text-right text-text">
                    {formatInt(p.new_creatives)}
                  </td>
                  <td className="num px-3 py-1.5 text-right text-text">
                    {formatInt(p.advertisers)}
                  </td>
                  <td className="px-3 py-1 text-center">
                    <ScoreChip score={p.winner_score} />
                  </td>
                  <td className="py-1.5 pr-4 text-right md:pr-5">
                    <Link
                      to={`/report/${p.snapshot_id}`}
                      className="text-signal underline-offset-2 hover:underline"
                    >
                      Open report
                      <span className="sr-only"> for {label}</span>
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ItemNotFound() {
  return (
    <section className="max-w-xl space-y-4 pt-16">
      <h1 className="text-24 font-semibold text-text">This watch item doesn&apos;t exist.</h1>
      <Link to="/watch" className={buttonVariants({ variant: "outline" })}>
        Open the watchlist
      </Link>
    </section>
  );
}
