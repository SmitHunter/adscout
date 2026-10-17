import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { ApiError, type WatchItem } from "@/api/client";
import {
  useAddWatch,
  useRemoveWatch,
  useRunWatch,
  useSetWatchEnabled,
  useSettings,
  useTimezone,
  useWatchItems,
} from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useDemo } from "@/features/demo/demo";
import { DemoLock } from "@/features/demo/DemoLock";
import { CountryToggle } from "@/features/search/CountryToggle";
import {
  formatChange,
  formatDateTime,
  formatInt,
  formatRelative,
  formatWeekdayTime,
} from "@/lib/format";
import { cn } from "@/lib/utils";

import { RemoveWatchDialog } from "./RemoveWatchDialog";
import { ScoreChip } from "./ScoreChip";
import { Sparkline } from "./Sparkline";
import { checkStatus, KIND_LABEL, nextRun, scopeLabel, TONE_TEXT, watchQuery } from "./watch";

const EMPTY_COPY = "Watch a product or advertiser to track it daily.";

export function WatchlistPage() {
  const items = useWatchItems();
  const settings = useSettings();
  const timezone = useTimezone();
  const list = items.data ?? [];
  const next = nextRun(list);

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div className="space-y-1">
          <h1 className="text-24 font-semibold tracking-[-0.01em] text-text">Watchlist</h1>
          <p className="text-13 text-muted">
            {settings.data ? (
              <>
                Checked once a day at{" "}
                <Link
                  to="/settings"
                  className="num text-12 text-text underline decoration-rule underline-offset-4 hover:decoration-muted"
                >
                  {settings.data.daily_check_time}
                </Link>
                , one search at a time.
              </>
            ) : (
              " "
            )}
          </p>
        </div>
        {items.isSuccess && list.length > 0 ? (
          <p className="text-13 text-muted">
            {next !== null ? (
              <>
                Next check{" "}
                <time dateTime={next} className="num text-text">
                  {formatWeekdayTime(next, timezone)}
                </time>
              </>
            ) : (
              "Daily checks are paused"
            )}
          </p>
        ) : null}
      </header>

      <AddWatchForm />

      {items.isPending ? (
        <div className="space-y-2 border border-rule bg-panel p-4" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : items.isError ? (
        <div className="space-y-1 border border-rule bg-panel px-5 py-6">
          <p className="text-13 text-text">Can&apos;t load the watchlist.</p>
          <p className="text-13 text-muted">
            {items.error instanceof ApiError ? items.error.detail : items.error.message}
          </p>
        </div>
      ) : list.length === 0 ? (
        <div className="border border-rule bg-panel px-5 py-10 md:px-6">
          <p className="text-15 text-text">{EMPTY_COPY}</p>
          <p className="mt-1 max-w-[60ch] text-13 text-pretty text-muted">
            Add a product keyword above. To watch a store, open a report and pick Watch on its row
            in the Advertisers tab.
          </p>
        </div>
      ) : (
        <WatchTable items={list} timezone={timezone} />
      )}
    </div>
  );
}

function AddWatchForm() {
  const settings = useSettings();
  const add = useAddWatch();
  const [keyword, setKeyword] = useState("");
  const [country, setCountry] = useState("US");
  const [error, setError] = useState<string | null>(null);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const query = keyword.trim();
    if (query === "") {
      setError("Enter a product keyword to watch.");
      return;
    }
    setError(null);
    add.mutate(
      watchQuery(
        {
          kind: "keyword",
          query,
          page_id: null,
          country,
          active_status: "active",
          media_type: "all",
        },
        settings.data?.max_results ?? 300,
      ),
      {
        onSuccess: (item) => {
          setKeyword("");
          toast.success(`Watching ${item.query} daily.`);
        },
        onError: (err) => setError(err instanceof ApiError ? err.detail : err.message),
      },
    );
  };

  return (
    <div>
      <form
        aria-label="Watch a product"
        onSubmit={submit}
        noValidate
        className="flex flex-wrap items-stretch gap-2 border border-rule bg-panel p-2 lg:flex-nowrap"
      >
        <label className="flex min-w-[60%] flex-1 items-center lg:min-w-0">
          <span className="sr-only">Product keyword</span>
          <input
            type="text"
            name="keyword"
            autoComplete="off"
            spellCheck={false}
            maxLength={100}
            placeholder="Product keyword to watch, like neck fan"
            value={keyword}
            onChange={(e) => {
              setKeyword(e.target.value);
              if (error !== null) setError(null);
            }}
            aria-invalid={error !== null || undefined}
            aria-describedby={error !== null ? "add-watch-error" : undefined}
            className="h-8 w-full min-w-0 rounded-sm border border-transparent bg-transparent px-2.5 text-15 text-text placeholder:text-muted focus-visible:outline-offset-0"
          />
        </label>
        <CountryToggle
          value={country}
          onChange={setCountry}
          euAvailable={settings.data?.has_meta_token ?? false}
        />
        <Button type="submit" disabled={add.isPending} className="px-5">
          Watch
        </Button>
      </form>
      {error !== null ? (
        <p id="add-watch-error" role="alert" className="mt-2 text-13 text-fail">
          {error}
        </p>
      ) : null}
    </div>
  );
}

const TH = "h-8 px-3 font-normal whitespace-nowrap";

function WatchTable({ items, timezone }: { items: WatchItem[]; timezone: string }) {
  const remove = useRemoveWatch();
  const [removing, setRemoving] = useState<WatchItem | null>(null);

  const confirmRemove = () => {
    const item = removing;
    if (item === null) return;
    remove.mutate(item.id, {
      onSuccess: () => toast.success(`Stopped watching ${item.query}.`),
      onError: (err) => toast.error(err instanceof ApiError ? err.detail : err.message),
    });
  };

  return (
    <div className="relative overflow-x-auto border border-rule bg-panel">
      <table className="w-full min-w-[1000px] font-dense text-13">
        <caption className="sr-only">Watched searches</caption>
        <thead>
          <tr className="border-b border-rule text-left text-12 text-muted">
            <th scope="col" className={cn(TH, "w-[24%] pl-4")}>
              Search
            </th>
            <th scope="col" className={TH}>
              Country
            </th>
            <th scope="col" className={TH}>
              Active ads, last 14 checks
            </th>
            <th scope="col" className={cn(TH, "text-center")}>
              Winner Score
            </th>
            <th scope="col" className={TH}>
              Last check
            </th>
            <th scope="col" className={TH}>
              Status
            </th>
            <th scope="col" className={TH}>
              Daily
            </th>
            <th scope="col" className={cn(TH, "pr-4")}>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <WatchRow
              key={item.id}
              item={item}
              timezone={timezone}
              onRemove={() => setRemoving(item)}
            />
          ))}
        </tbody>
      </table>
      <RemoveWatchDialog
        query={removing?.query ?? null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        onConfirm={confirmRemove}
      />
    </div>
  );
}

function WatchRow({
  item,
  timezone,
  onRemove,
}: {
  item: WatchItem;
  timezone: string;
  onRemove: () => void;
}) {
  const run = useRunWatch();
  const demo = useDemo();
  const setEnabled = useSetWatchEnabled();
  const status = checkStatus(item);
  const spark = item.sparkline;
  const latest = item.latest_point?.active_ads ?? spark.at(-1);
  const prev = spark.length > 1 ? spark.at(-2) : undefined;
  const capped = item.latest_point?.capped ?? false;
  // A capped count is a floor: a change from it would read as more precise than it is.
  const change =
    latest !== undefined && prev !== undefined && !capped ? formatChange(prev, latest) : null;

  const runNow = () =>
    run.mutate(item.id, {
      onSuccess: (res) => toast.success(res.message || `Checking ${item.query} now.`),
      onError: (err) => toast.error(err instanceof ApiError ? err.detail : err.message),
    });

  return (
    <tr
      className={cn(
        "border-b border-rule transition-colors last:border-b-0 hover:bg-rule/25",
        !item.enabled && "text-muted",
      )}
    >
      <td className="max-w-[320px] py-2 pr-3 pl-4">
        <Link
          to={`/watch/${item.id}`}
          className={cn(
            "block truncate text-15 font-medium underline-offset-4 hover:underline",
            item.enabled ? "text-text" : "text-muted",
          )}
          title={item.query}
        >
          {item.query}
        </Link>
        <span className="mt-0.5 flex items-baseline gap-3 text-12 text-muted">
          {KIND_LABEL[item.kind]}
          {item.unread_alerts > 0 ? (
            <span className="text-signal">
              <span className="num">{item.unread_alerts}</span>{" "}
              {item.unread_alerts === 1 ? "new alert" : "new alerts"}
            </span>
          ) : null}
        </span>
      </td>
      <td className="px-3 py-2 align-middle">
        <span className={item.enabled ? "text-text" : "text-muted"}>
          {scopeLabel(item.country)}
        </span>
      </td>
      <td className="px-3 py-2">
        <div className={cn("flex items-center gap-4", !item.enabled && "opacity-60")}>
          <Sparkline values={spark} />
          <span className="flex w-24 flex-col items-end leading-tight">
            <span className="num text-15 text-text">
              {latest !== undefined ? formatInt(latest) : "—"}
              {capped ? <span className="text-muted">+</span> : null}
            </span>
            <span className="num text-12 text-muted">{change ?? " "}</span>
          </span>
        </div>
      </td>
      <td className="px-3 py-2 text-center">
        <ScoreChip score={item.latest_point?.winner_score} />
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        {item.last_run_at ? (
          <time
            dateTime={item.last_run_at}
            title={formatDateTime(item.last_run_at, timezone)}
            className="text-text"
          >
            {formatRelative(item.last_run_at, new Date(), timezone)}
          </time>
        ) : (
          <span className="text-muted">Never</span>
        )}
      </td>
      <td className={cn("px-3 py-2 whitespace-nowrap", TONE_TEXT[status.tone])}>
        <span className="inline-flex items-center gap-1.5">
          {status.tone === "signal" ? (
            <span
              aria-hidden="true"
              className="size-1.5 animate-pulse rounded-full bg-signal motion-reduce:animate-none"
            />
          ) : null}
          {status.text}
        </span>
      </td>
      <td className="px-3 py-2">
        <Switch
          checked={item.enabled}
          aria-label={`Check ${item.query} daily`}
          onCheckedChange={(enabled) =>
            setEnabled.mutate(
              { id: item.id, enabled },
              {
                onError: (err) => toast.error(err instanceof ApiError ? err.detail : err.message),
              },
            )
          }
        />
      </td>
      <td className="py-2 pr-4 pl-3">
        <div className="flex justify-end gap-1">
          <DemoLock locked={demo}>
            <Button
              variant="outline"
              size="sm"
              aria-label={`Run ${item.query} now`}
              disabled={item.running || run.isPending || demo}
              onClick={runNow}
            >
              {item.running ? "Checking" : "Run now"}
            </Button>
          </DemoLock>
          {/* A finishing check would raise an alert for a removed item: the API refuses it. */}
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Remove ${item.query}`}
            disabled={item.running}
            onClick={onRemove}
          >
            Remove
          </Button>
        </div>
      </td>
    </tr>
  );
}
