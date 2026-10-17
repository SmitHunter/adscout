import type { Schemas } from "@/api/client";
import { Numerals } from "@/features/search/Numerals";
import { formatInt } from "@/lib/format";
import { useCountUp } from "@/lib/motion";
import { cn } from "@/lib/utils";

import { scoreBand } from "./ruler";

type WinnerScore = Schemas["WinnerScore"];

const BAND_TEXT = { pass: "text-pass", warn: "text-warn", fail: "text-fail" } as const;

const SATURATION: Record<WinnerScore["saturation"], { label: string; className: string }> = {
  early: { label: "Early", className: "border-signal/50 text-signal" },
  healthy: { label: "Healthy", className: "border-pass/50 text-pass" },
  crowded: { label: "Crowded", className: "border-warn/50 text-warn" },
};

/** Sets numbers in the numeral face inside a sentence. */
function Num({ children }: { children: number }) {
  return <span className="num">{formatInt(children)}</span>;
}

/**
 * `animate={false}` shows the score at once and `warnings={false}` drops the
 * in-app notes (the print page has no motion and no Excluded tab to point to).
 */
export function ScoreBlock({
  score,
  animate = true,
  warnings = true,
}: {
  score: WinnerScore;
  animate?: boolean;
  warnings?: boolean;
}) {
  const counted = useCountUp(score.headline);
  const shown = animate ? counted : score.headline;
  const band = scoreBand(score.headline);
  const leader = score.leader;
  const saturation = SATURATION[score.saturation];

  return (
    <section aria-labelledby="score-heading" className="flex min-w-0 flex-1 flex-col">
      <h2 id="score-heading" className="text-13 text-muted">
        Winner Score
      </h2>

      <p className="mt-2 flex items-baseline gap-2">
        {/* Values here are ≤ 3 digits: no separators at display size. */}
        <span
          data-testid="winner-score"
          aria-hidden="true"
          className={cn("num text-72 font-medium tracking-[-0.06em] tabular-nums", BAND_TEXT[band])}
        >
          {shown}
        </span>
        <span className="num text-15 text-muted" aria-hidden="true">
          /100
        </span>
        <span className="sr-only">{score.headline} out of 100</span>
      </p>

      {leader !== null ? (
        <div className="mt-3">
          <p className="truncate text-15 font-medium text-text">{leader.page_name}</p>
          <p data-testid="leader-counts" className="text-13 text-muted">
            <Num>{leader.ads}</Num> {leader.ads === 1 ? "ad" : "ads"}, <Num>{leader.creatives}</Num>{" "}
            {leader.creatives === 1 ? "creative" : "creatives"}
          </p>
        </div>
      ) : (
        <p className="mt-3 text-13 text-muted">No single advertiser leads. Scored on the market.</p>
      )}

      <div className="mt-5 border-t border-rule pt-4">
        <div className="flex items-baseline justify-between gap-3">
          <p className="text-13 text-muted">
            Market demand{" "}
            <span data-testid="market-demand" className="num ml-1 text-24 font-medium text-text">
              {score.total}
            </span>
          </p>
          <span
            className={cn(
              "shrink-0 rounded-sm border px-1.5 py-px text-12 font-medium",
              saturation.className,
            )}
          >
            {saturation.label}
          </span>
        </div>
        <p className="mt-1 text-13 text-muted">
          <Num>{score.advertisers}</Num> {score.advertisers === 1 ? "advertiser" : "advertisers"}{" "}
          running relevant ads
        </p>
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-x-3 border-t border-rule pt-3 text-12">
        <Stat label="Relevant ads" value={score.relevant_ads} />
        <Stat label="Excluded" value={score.excluded_ads} />
        <Stat label="Creatives" value={score.creatives} />
      </dl>

      {warnings && score.warnings.length > 0 ? (
        <ul className="mt-4 space-y-1 border-t border-rule pt-3 text-12 text-pretty text-muted">
          {score.warnings.map((warning) => (
            <li key={warning}>
              <Numerals text={warning} />
            </li>
          ))}
        </ul>
      ) : null}

      {score.provisional ? (
        <p className="mt-auto pt-5">
          <span className="inline-block rounded-sm border border-muted/60 px-2 py-0.5 text-12 text-muted">
            Provisional: angle analysis not run yet
          </span>
        </p>
      ) : null}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    // Reversed, so justify-end packs from the top: the numbers share a line even
    // when a narrow column (the printed report) wraps a label.
    <div className="flex flex-col-reverse justify-end gap-0.5">
      <dt className="text-muted">{label}</dt>
      <dd className="num text-15 text-text">{formatInt(value)}</dd>
    </div>
  );
}
