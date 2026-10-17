import { useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

import { formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

import { landingHref, type AdvertiserRow } from "./gallery";
import { RelevanceToggle } from "./RelevanceToggle";

type Column = "name" | "creatives" | "ads" | "days" | "domain";
type Direction = "asc" | "desc";

const COLUMNS: { key: Column; label: string; numeric: boolean; first: Direction }[] = [
  { key: "name", label: "Advertiser", numeric: false, first: "asc" },
  { key: "creatives", label: "Creatives", numeric: true, first: "desc" },
  { key: "ads", label: "Ads", numeric: true, first: "desc" },
  { key: "days", label: "Oldest ad (days)", numeric: true, first: "desc" },
  { key: "domain", label: "Landing site", numeric: false, first: "asc" },
];

function value(row: AdvertiserRow, column: Column): number | string {
  switch (column) {
    case "name":
      return row.name.toLowerCase();
    case "creatives":
      return row.creatives;
    case "ads":
      return row.ads;
    case "days":
      return row.days_running_max;
    case "domain":
      return row.landing_domain?.toLowerCase() ?? "￿";
  }
}

interface AdvertiserTableProps {
  /** As the API sends them: most creatives first. */
  rows: AdvertiserRow[];
  /** The advertiser whose override is in flight. */
  pendingKey: string | null;
  onToggle: (row: AdvertiserRow) => void;
  /** A per-row Watch control, when the page offers advertiser watches. */
  renderWatch?: (row: AdvertiserRow) => ReactNode;
}

export function AdvertiserTable({ rows, pendingKey, onToggle, renderWatch }: AdvertiserTableProps) {
  // null: the API's order (creatives desc, then ads).
  const [sort, setSort] = useState<{ column: Column; direction: Direction } | null>(null);

  const sorted = useMemo(() => {
    if (sort === null) return rows;
    const sign = sort.direction === "asc" ? 1 : -1;
    return rows
      .map((row, index) => ({ row, index }))
      .sort((a, b) => {
        const x = value(a.row, sort.column);
        const y = value(b.row, sort.column);
        const order =
          typeof x === "number" && typeof y === "number"
            ? x - y
            : String(x).localeCompare(String(y));
        return order * sign || a.index - b.index;
      })
      .map(({ row }) => row);
  }, [rows, sort]);

  const activeColumn = sort?.column ?? "creatives";
  const activeDirection = sort?.direction ?? "desc";

  if (rows.length === 0) {
    return (
      <p className="border border-rule bg-panel px-5 py-8 text-13 text-muted">
        No advertisers in this snapshot.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto border border-rule bg-panel">
      <table className="w-full min-w-[720px] font-dense text-13">
        <caption className="sr-only">Advertisers, sortable by column</caption>
        <thead>
          <tr className="border-b border-rule text-12 text-muted">
            {COLUMNS.map((column) => {
              const active = activeColumn === column.key;
              const Arrow = activeDirection === "asc" ? ArrowUp : ArrowDown;
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={
                    active ? (activeDirection === "asc" ? "ascending" : "descending") : undefined
                  }
                  className={cn(
                    "px-3 py-0 font-normal first:pl-4",
                    column.numeric ? "w-32 text-right" : "text-left",
                  )}
                >
                  <button
                    type="button"
                    onClick={() =>
                      setSort((prev) => ({
                        column: column.key,
                        direction:
                          (prev?.column ?? "creatives") === column.key
                            ? (prev?.direction ?? "desc") === "asc"
                              ? "desc"
                              : "asc"
                            : column.first,
                      }))
                    }
                    className={cn(
                      "inline-flex h-8 cursor-pointer items-center gap-1 whitespace-nowrap hover:text-text focus-visible:-outline-offset-2",
                      column.numeric && "flex-row-reverse",
                      active && "text-text",
                    )}
                  >
                    {column.label}
                    <Arrow
                      className={cn("size-3", active ? "text-signal" : "invisible")}
                      aria-hidden="true"
                    />
                  </button>
                </th>
              );
            })}
            {renderWatch ? (
              <th scope="col" className="w-28 px-3 py-0 text-right font-normal">
                <span className="sr-only">Watch</span>
              </th>
            ) : null}
            <th scope="col" className="w-24 py-0 pr-4 pl-3 text-right font-normal">
              <span className="sr-only">Relevance</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <AdvertiserTableRow
              key={row.key}
              row={row}
              pending={pendingKey === row.key}
              onToggle={onToggle}
              watch={renderWatch?.(row)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AdvertiserTableRow({
  row,
  pending,
  onToggle,
  watch,
}: {
  row: AdvertiserRow;
  pending: boolean;
  onToggle: (row: AdvertiserRow) => void;
  watch?: ReactNode;
}) {
  const href = landingHref(row.landing_domain);
  const dim = row.excluded ? "text-muted" : "text-text";
  return (
    <tr className={cn("border-b border-rule last:border-b-0", pending && "opacity-60")}>
      <td className="max-w-0 py-1.5 pr-3 pl-4">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={cn(
              "truncate font-medium",
              dim,
              row.excluded && "line-through decoration-muted/60",
            )}
            title={row.name}
          >
            {row.name}
          </span>
          {row.is_leader ? (
            <span className="shrink-0 rounded-sm border border-signal/60 px-1.5 text-12 text-signal">
              Leader
            </span>
          ) : null}
          {row.excluded ? (
            <span className="shrink-0 rounded-sm border border-rule px-1.5 text-12 text-muted">
              Excluded
            </span>
          ) : null}
        </div>
      </td>
      <td className={cn("num px-3 py-1.5 text-right", dim)}>{formatInt(row.creatives)}</td>
      <td className={cn("num px-3 py-1.5 text-right", dim)}>
        {formatInt(row.ads)}
        {row.total_ads !== row.ads ? (
          <span className="text-muted" title={`${row.total_ads} ads in total`}>
            /{formatInt(row.total_ads)}
          </span>
        ) : null}
      </td>
      <td className={cn("num px-3 py-1.5 text-right", dim)}>{formatInt(row.days_running_max)}</td>
      <td className="max-w-0 px-3 py-1.5">
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="block truncate text-signal underline-offset-2 hover:underline"
          >
            {row.landing_domain}
          </a>
        ) : (
          <span className="block truncate text-muted">
            {row.landing_domain ?? (
              <>
                <span aria-hidden="true">—</span>
                <span className="sr-only">No landing site</span>
              </>
            )}
          </span>
        )}
      </td>
      {watch !== undefined ? <td className="px-3 py-1 text-right">{watch}</td> : null}
      <td className="py-1 pr-4 pl-3 text-right">
        <RelevanceToggle
          excluded={row.excluded}
          subject={row.name}
          pending={pending}
          onToggle={() => onToggle(row)}
          className="-mr-1.5"
        />
      </td>
    </tr>
  );
}
