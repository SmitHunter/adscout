import { ActivityIcon, ColumnsIcon, PlusIcon, SlidersHorizontalIcon } from "lucide-react";
import { Link, NavLink, Outlet } from "react-router";

import { ApiError, type Snapshot } from "@/api/client";
import { useAlerts, useSettings, useSnapshots, useTimezone } from "@/api/hooks";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { DemoBanner } from "@/features/demo/DemoBanner";
import { AlertsMenu } from "@/features/watch/AlertsMenu";
import { formatDate, formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

import { latestPerSearch } from "./recent";
import { ThemeToggle } from "./ThemeToggle";

export function Shell() {
  return (
    <div className="flex min-h-dvh flex-col md:h-dvh">
      <DemoBanner />
      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[var(--rail-width)_minmax(0,1fr)]">
        <Rail />
        {/* `relative`: the scroller is the containing block for absolutely positioned content
            (sr-only text, chart tooltips), so it can't stretch the page past the viewport. */}
        <main className="relative min-w-0 md:overflow-y-auto">
          <div className="mx-auto w-full max-w-(--main-max) px-4 py-6 md:px-10 md:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

function Rail() {
  const settings = useSettings();

  return (
    <aside
      aria-label="AdScout"
      className="flex min-h-0 flex-col border-b border-rule bg-panel md:border-r md:border-b-0"
    >
      <div className="flex h-14 shrink-0 items-center px-4">
        <Link
          to="/"
          className="text-24 leading-none font-bold tracking-[-0.01em] text-text font-dense"
        >
          AdScout
        </Link>
      </div>

      <div className="shrink-0 space-y-1 px-4 pb-4">
        <Link to="/" className={cn(buttonVariants({ variant: "outline", size: "sm" }), "w-full")}>
          <PlusIcon aria-hidden="true" />
          New search
        </Link>
        <WatchlistNav />
        <NavLink
          to="/compare"
          className={({ isActive }) =>
            cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              "w-full justify-start",
              isActive && "bg-rule/50 text-text",
            )
          }
        >
          <ColumnsIcon aria-hidden="true" />
          Compare
        </NavLink>
      </div>

      <nav
        aria-labelledby="recent-heading"
        className="flex min-h-0 flex-1 flex-col border-t border-rule"
      >
        <h2 id="recent-heading" className="px-4 pt-4 pb-2 text-12 font-medium text-muted">
          Recent searches
        </h2>
        <RecentSearches />
      </nav>

      <div className="shrink-0 space-y-3 border-t border-rule px-4 py-4">
        <NavLink
          to="/settings"
          className={({ isActive }) =>
            cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              "w-full justify-start",
              isActive && "bg-rule/50 text-text",
            )
          }
        >
          <SlidersHorizontalIcon aria-hidden="true" />
          Settings
        </NavLink>
        <ThemeToggle />
        <div className="flex items-baseline justify-between gap-2 text-12 text-muted">
          <span className="truncate font-dense">
            {settings.data ? `Times in ${settings.data.timezone}` : " "}
          </span>
          {settings.data ? <span className="num shrink-0">v{settings.data.version}</span> : null}
        </div>
      </div>
    </aside>
  );
}

/** The Watchlist link with its unread-alert count, beside the alerts menu. */
function WatchlistNav() {
  const alerts = useAlerts();
  const unread = (alerts.data ?? []).filter((a) => !a.read).length;

  return (
    <div className="flex items-center gap-1 pt-2">
      <NavLink
        to="/watch"
        aria-label={
          unread > 0
            ? `Watchlist, ${unread} unread ${unread === 1 ? "alert" : "alerts"}`
            : "Watchlist"
        }
        className={({ isActive }) =>
          cn(
            buttonVariants({ variant: "ghost", size: "sm" }),
            "min-w-0 flex-1 justify-start",
            isActive && "bg-rule/50 text-text",
          )
        }
      >
        <ActivityIcon aria-hidden="true" />
        Watchlist
        {unread > 0 ? (
          <span className="num ml-auto inline-flex h-4 min-w-4 items-center justify-center rounded-sm bg-signal px-1 text-12 leading-none font-medium text-bg">
            {unread}
          </span>
        ) : null}
      </NavLink>
      <AlertsMenu />
    </div>
  );
}

function RecentSearches() {
  const snapshots = useSnapshots();
  const timezone = useTimezone();

  if (snapshots.isPending) {
    return (
      <div className="space-y-4 px-4 py-2" aria-busy="true" aria-label="Loading recent searches">
        {[0, 1, 2].map((i) => (
          <div key={i} className="space-y-1.5">
            <Skeleton className="h-3.5 w-32" />
            <Skeleton className="h-3 w-full" />
          </div>
        ))}
      </div>
    );
  }

  if (snapshots.isError) {
    const detail =
      snapshots.error instanceof ApiError ? snapshots.error.detail : snapshots.error.message;
    return (
      <div className="space-y-1 px-4 py-2 text-12">
        <p className="text-text">Can't load recent searches.</p>
        <p className="text-muted">{detail}</p>
      </div>
    );
  }

  if (snapshots.data.length === 0) {
    return <p className="px-4 py-2 text-12 text-muted">Your searches will appear here.</p>;
  }

  return (
    <ul className="min-h-0 flex-1 overflow-y-auto pb-2 max-md:max-h-64">
      {latestPerSearch(snapshots.data).map((snapshot) => (
        <li key={snapshot.id}>
          <RecentSearchLink snapshot={snapshot} timezone={timezone} />
        </li>
      ))}
    </ul>
  );
}

const STATUS_LABEL: Record<string, { text: string; className: string } | undefined> = {
  queued: { text: "Queued", className: "text-muted" },
  running: { text: "Running", className: "text-signal" },
  blocked: { text: "Blocked", className: "text-warn" },
  error: { text: "Failed", className: "text-fail" },
  cancelled: { text: "Cancelled", className: "text-muted" },
};

function RecentSearchLink({ snapshot, timezone }: { snapshot: Snapshot; timezone: string }) {
  const status = STATUS_LABEL[snapshot.status];

  return (
    <NavLink
      to={`/report/${snapshot.id}`}
      className={({ isActive }) =>
        cn(
          "relative block px-4 py-2 transition-colors hover:bg-rule/40 focus-visible:-outline-offset-2",
          isActive &&
            "bg-rule/50 before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-signal",
        )
      }
    >
      <span className="flex items-baseline justify-between gap-2">
        <span className="truncate text-13 font-medium text-text">{snapshot.query}</span>
        {status ? (
          <span className={cn("shrink-0 text-12 font-dense", status.className)}>{status.text}</span>
        ) : null}
      </span>
      <span className="mt-1 flex items-baseline gap-x-3 text-12 text-muted">
        <span className="font-dense">{snapshot.country}</span>
        <span className="font-dense">
          <span className="num">{formatInt(snapshot.ad_count)}</span> ads
        </span>
        <time className="num ml-auto" dateTime={snapshot.started_at}>
          {formatDate(snapshot.started_at, timezone)}
        </time>
      </span>
    </NavLink>
  );
}
