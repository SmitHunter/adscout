import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";

import type { Schemas } from "@/api/client";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Numerals } from "@/features/search/Numerals";
import { cn } from "@/lib/utils";

import {
  formatAmount,
  layoutLabels,
  percent,
  rulerMax,
  rulerOverflows,
  rulerTicks,
  rulerZones,
  STATUS_TEXT,
  ZONE_BG,
} from "./ruler";

type FactorResult = Schemas["FactorResult"];
type WinnerScore = Schemas["WinnerScore"];

const LABEL_GAP = 12;
/** Room at the track's right end for an overflowing marker and its arrow. */
const OVERFLOW_ROOM = "right-[14px]";

/**
 * One factor as a ruler: a track from 0 past its thresholds, ticks at fail,
 * pass and full, and a marker at the value in the status colour. Value against
 * threshold reads without reading a number.
 */
export function FactorRuler({ factor }: { factor: FactorResult }) {
  const max = rulerMax(factor);
  const ticks = rulerTicks(factor);
  const pending = factor.status === "pending" || factor.value === null;
  const at = factor.value === null ? undefined : percent(factor.value, max);
  const overflow = !pending && rulerOverflows(factor);

  return (
    <li className="py-3.5">
      {/* Narrow columns (compare) wrap whole phrases, never mid-phrase. */}
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-13 font-medium whitespace-nowrap text-text">{factor.label}</span>
        <span className={cn("text-13 whitespace-nowrap", pending ? "text-muted" : "text-text")}>
          <Numerals text={factor.display} />
        </span>
        <span data-testid="ruler-points" className="num ml-auto text-13 whitespace-nowrap">
          <span className={pending ? "text-muted" : "text-text"}>
            {factor.points === null ? "—" : formatAmount(factor.points)}
          </span>
          <span className="text-muted">/{formatAmount(factor.max_points)}</span>
        </span>
      </div>

      {/* 20 px zone: pointer 0–8, track 9–15, ticks 5–19, needle 6–18. */}
      <div
        data-testid="ruler-track"
        data-hatched={pending ? "true" : "false"}
        data-overflow={overflow ? "true" : "false"}
        className="relative mt-1.5 h-5"
        aria-hidden="true"
      >
        {/* The scale; an overflowing value leaves room past its end for the marker. */}
        <div className={cn("absolute inset-y-0 left-0", overflow ? OVERFLOW_ROOM : "right-0")}>
          {pending ? (
            <div className="ruler-hatch absolute inset-x-0 top-[9px] h-1.5" />
          ) : (
            <div className="absolute inset-x-0 top-[9px] flex h-1.5 bg-rule">
              {rulerZones(factor, max).map((zone) => (
                <span
                  key={zone.status}
                  data-testid="ruler-zone"
                  className={cn("h-full", ZONE_BG[zone.status])}
                  style={{ width: zone.width }}
                />
              ))}
            </div>
          )}
          {ticks.map((tick) => (
            <span
              key={tick.kind}
              data-testid="ruler-tick"
              className="absolute top-[5px] h-[14px] w-px -translate-x-1/2 bg-muted"
              style={{ left: percent(tick.at, max) }}
            />
          ))}
          {at !== undefined && !pending ? (
            <span
              data-testid="ruler-marker"
              data-status={factor.status}
              className={cn(
                // Centre clamped 6 px in from each end so a 0 or a max never overhangs;
                // past the end it sits in the room the scale left for it.
                "ruler-marker absolute inset-y-0 w-3 -translate-x-1/2",
                overflow ? "left-(--at)" : "left-[clamp(6px,var(--at),calc(100%-6px))]",
                STATUS_TEXT[factor.status],
              )}
              style={{ "--at": at } as CSSProperties}
            >
              <span className="absolute top-0 left-0 h-[9px] w-3 bg-current [clip-path:polygon(0_0,100%_0,50%_100%)]" />
              <span className="absolute top-1.5 left-1/2 h-3 w-0.5 -translate-x-1/2 bg-current" />
            </span>
          ) : null}
        </div>
        {overflow ? (
          // The value runs off the scale: an arrow at the end says "further than shown".
          <span
            data-testid="ruler-overflow"
            className={cn(
              "absolute top-[8px] right-0 h-2 w-1.5 bg-current [clip-path:polygon(0_0,100%_50%,0_100%)]",
              STATUS_TEXT[factor.status],
            )}
          />
        ) : null}
      </div>

      <TickLabels ticks={ticks} max={max} className={overflow ? "mr-[14px]" : undefined} />

      <p className="mt-0.5 text-12 text-muted">
        <Numerals text={factor.detail} />
      </p>
      <p className="sr-only">
        {factor.label}: {factor.display}, {factor.status}
        {overflow ? ", beyond the end of the scale" : ""}. Thresholds:{" "}
        {ticks.map((tick) => `${tick.prefix} ${formatAmount(tick.at)}`).join(", ")}.
      </p>
    </li>
  );
}

/** Threshold labels under their ticks, nudged apart where ticks crowd together. */
function TickLabels({
  ticks,
  max,
  className,
}: {
  ticks: ReturnType<typeof rulerTicks>;
  max: number;
  className?: string | undefined;
}) {
  const rowRef = useRef<HTMLDivElement>(null);
  const [lefts, setLefts] = useState<number[] | null>(null);
  const signature = ticks.map((t) => t.at).join(",") + `/${max}`;

  useLayoutEffect(() => {
    const row = rowRef.current;
    if (row === null) return;
    const place = () => {
      const width = row.clientWidth;
      if (width === 0) return;
      const labels = Array.from(row.children) as HTMLElement[];
      const widths = labels.map((label) => label.offsetWidth);
      const desired = ticks.map((tick) => (Math.min(tick.at / max, 1) || 0) * width);
      setLefts(layoutLabels(desired, widths, width, LABEL_GAP));
    };
    place();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(place);
    observer.observe(row);
    return () => observer.disconnect();
    // `signature` captures every input that moves a label.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  return (
    <div
      ref={rowRef}
      className={cn("relative mt-1 h-4 text-12 text-muted", className)}
      aria-hidden="true"
    >
      {ticks.map((tick, i) => (
        <span
          key={tick.kind}
          className="absolute top-0 whitespace-nowrap"
          style={{ left: lefts?.[i] ?? percent(tick.at, max) }}
        >
          {tick.prefix} <span className="num">{formatAmount(tick.at)}</span>
        </span>
      ))}
    </div>
  );
}

type View = "market" | "leader";

/**
 * With a leader, its own factors first (the headline Winner Score is its score),
 * and the market's behind a small toggle; without one, the market's alone.
 */
export function FactorPanel({ score }: { score: WinnerScore }) {
  const leader = score.leader;
  const [view, setView] = useState<View>(leader !== null ? "leader" : "market");
  const showLeader = view === "leader" && leader !== null;
  const factors = showLeader ? leader.factors : score.factors;

  return (
    <section aria-labelledby="factors-heading" className="min-w-0">
      <h2 id="factors-heading" className="sr-only">
        Score factors
      </h2>
      <div className="flex h-[26px] items-center justify-between gap-4">
        {leader !== null ? (
          <ToggleGroup
            type="single"
            size="sm"
            value={view}
            onValueChange={(next) => {
              if (next === "market" || next === "leader") setView(next);
            }}
            aria-label="Show factors for"
          >
            <ToggleGroupItem value="leader" className="max-w-48">
              <span className="truncate">{leader.page_name}</span>
            </ToggleGroupItem>
            <ToggleGroupItem value="market">Market</ToggleGroupItem>
          </ToggleGroup>
        ) : (
          <span className="text-13 text-muted">Market</span>
        )}
        <span className="text-12 text-muted">Points</span>
      </div>
      <ul className="mt-1 divide-y divide-rule">
        {factors.map((factor) => (
          <FactorRuler key={factor.key} factor={factor} />
        ))}
      </ul>
    </section>
  );
}
