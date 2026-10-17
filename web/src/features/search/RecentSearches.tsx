import { Link, useNavigate } from "react-router";

import type { Snapshot } from "@/api/client";
import { useTimezone } from "@/api/hooks";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatDateTime, formatInt, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

import { Numerals } from "./Numerals";

const STATUS: Record<string, { text: string; className: string } | undefined> = {
  ok: { text: "Done", className: "text-text" },
  capped: { text: "Hit limit", className: "text-text" },
  queued: { text: "Queued", className: "text-muted" },
  running: { text: "Running", className: "text-signal" },
  blocked: { text: "Blocked", className: "text-warn" },
  error: { text: "Failed", className: "text-fail" },
  cancelled: { text: "Cancelled", className: "text-muted" },
};

/** How many recent searches the home table shows; the rail lists them all. */
const ROWS = 12;

export function RecentSearches({ snapshots }: { snapshots: Snapshot[] }) {
  const timezone = useTimezone();
  const navigate = useNavigate();
  const now = new Date();

  return (
    <section aria-labelledby="home-recent-heading" className="space-y-2">
      <h2 id="home-recent-heading" className="text-13 font-medium text-muted">
        Recent searches
      </h2>
      <div className="overflow-x-auto border border-rule bg-panel">
        <table aria-labelledby="home-recent-heading" className="w-full font-dense text-13">
          <thead>
            <tr className="border-b border-rule text-left text-12 text-muted">
              <th scope="col" className="py-2 pr-3 pl-4 font-normal">
                Keyword
              </th>
              <th scope="col" className="w-24 px-3 py-2 font-normal">
                Country
              </th>
              <th scope="col" className="w-24 px-3 py-2 text-right font-normal">
                Ads
              </th>
              <th scope="col" className="w-28 px-3 py-2 font-normal">
                Status
              </th>
              <th scope="col" className="w-32 py-2 pr-4 pl-3 text-right font-normal">
                When
              </th>
            </tr>
          </thead>
          <tbody>
            {snapshots.slice(0, ROWS).map((snapshot) => {
              const status = STATUS[snapshot.status];
              return (
                <tr
                  key={snapshot.id}
                  className="relative border-b border-rule transition-colors last:border-b-0 hover:bg-rule/40"
                >
                  <td className="max-w-0 py-2 pr-3 pl-4">
                    {/* The link covers the whole row; the time sits above it as its own focusable tooltip trigger and still navigates on click. */}
                    <Link
                      to={`/report/${snapshot.id}`}
                      className="block truncate font-medium text-text after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-signal"
                    >
                      {snapshot.query}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-muted">{snapshot.country}</td>
                  <td className="num px-3 py-2 text-right text-text">
                    {formatInt(snapshot.ad_count)}
                  </td>
                  <td className={cn("px-3 py-2", status?.className ?? "text-muted")}>
                    {status?.text ?? snapshot.status}
                  </td>
                  <td className="py-2 pr-4 pl-3 text-right text-muted">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <time
                          dateTime={snapshot.started_at}
                          tabIndex={0}
                          onClick={() => void navigate(`/report/${snapshot.id}`)}
                          onKeyDown={(event) => {
                            // Focusable like the row's link, so it opens the report the same way.
                            if (event.key === "Enter") void navigate(`/report/${snapshot.id}`);
                          }}
                          className="relative z-10 cursor-pointer"
                        >
                          <Numerals text={formatRelative(snapshot.started_at, now, timezone)} />
                        </time>
                      </TooltipTrigger>
                      <TooltipContent side="left">
                        <span className="num">{formatDateTime(snapshot.started_at, timezone)}</span>
                      </TooltipContent>
                    </Tooltip>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
