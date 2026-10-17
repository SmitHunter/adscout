import { useState } from "react";
import { BellIcon } from "lucide-react";
import { Link } from "react-router";

import type { WatchAlert } from "@/api/client";
import { useAlerts, useMarkAlertsRead, useTimezone } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The newest alerts the menu lists; the item pages keep the rest. */
const MENU_LIMIT = 30;

/** The rail's bell: a popover of recent alerts, newest first, with `Mark all read`. */
export function AlertsMenu() {
  const alerts = useAlerts();
  const markRead = useMarkAlertsRead();
  const timezone = useTimezone();
  const [open, setOpen] = useState(false);
  const all = alerts.data ?? [];
  const unread = all.filter((a) => !a.read).length;
  const shown = all.slice(0, MENU_LIMIT);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={unread > 0 ? `Alerts, ${unread} unread` : "Alerts"}
          className="relative"
        >
          <BellIcon aria-hidden="true" />
          {unread > 0 ? (
            <span
              aria-hidden="true"
              className="absolute top-1 right-1 size-1.5 rounded-full bg-signal ring-2 ring-panel"
            />
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent aria-label="Alerts" side="right" align="start" className="w-[360px]">
        <header className="flex h-11 items-center justify-between gap-4 border-b border-rule pr-2 pl-4">
          <h2 className="text-15 font-medium text-text">
            Alerts
            {unread > 0 ? (
              <span className="ml-2 text-13 font-normal text-muted">
                <span className="num text-12">{unread}</span> unread
              </span>
            ) : null}
          </h2>
          <Button
            variant="ghost"
            size="sm"
            disabled={unread === 0 || markRead.isPending}
            onClick={() => markRead.mutate({ all: true })}
          >
            Mark all read
          </Button>
        </header>
        {shown.length === 0 ? (
          <p className="px-4 py-5 text-13 text-pretty text-muted">
            No alerts yet. AdScout raises one when a watched search&apos;s active ads move 30% or
            more between daily checks.
          </p>
        ) : (
          <ul className="max-h-[min(420px,60dvh)] divide-y divide-rule overflow-y-auto">
            {shown.map((alert) => (
              <li key={alert.id}>
                <Link
                  to={`/watch/${alert.watch_item_id}`}
                  className="block hover:bg-rule/30 focus-visible:-outline-offset-2"
                  onClick={() => {
                    if (!alert.read) markRead.mutate({ ids: [alert.id], all: false });
                    setOpen(false);
                  }}
                >
                  <AlertBody alert={alert} timezone={timezone} />
                </Link>
              </li>
            ))}
          </ul>
        )}
        <footer className="border-t border-rule px-4 py-2">
          <Link
            to="/watch"
            onClick={() => setOpen(false)}
            className="text-13 text-signal underline-offset-2 hover:underline"
          >
            Open the watchlist
          </Link>
        </footer>
      </PopoverContent>
    </Popover>
  );
}

/** One alert: its message (unread ones carry the signal bar) and when it was raised. */
export function AlertBody({
  alert,
  timezone,
  withQuery = true,
}: {
  alert: WatchAlert;
  timezone: string;
  /** Name the search on a blocked alert; off where the page already names it. */
  withQuery?: boolean;
}) {
  return (
    <div
      className={cn(
        "relative px-4 py-2.5",
        !alert.read &&
          "before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-signal",
      )}
    >
      <p
        className={cn(
          "text-13 text-pretty",
          alert.read ? "text-muted" : alert.kind === "blocked" ? "text-warn" : "text-text",
        )}
      >
        {alert.message}
        {!alert.read ? <span className="sr-only"> (unread)</span> : null}
      </p>
      <p className="mt-0.5 flex items-baseline gap-3 text-12 text-muted">
        {/* A change message names its search; a blocked one doesn't. */}
        {withQuery && alert.kind === "blocked" ? (
          <span className="text-text">{alert.query}</span>
        ) : null}
        <time dateTime={alert.created_at} title={formatDateTime(alert.created_at, timezone)}>
          {formatRelative(alert.created_at, new Date(), timezone)}
        </time>
      </p>
    </div>
  );
}
