import { scoreBand } from "@/features/report/ruler";
import { cn } from "@/lib/utils";

const SCORE_CHIP = {
  pass: "border-pass/60 text-pass",
  warn: "border-warn/60 text-warn",
  fail: "border-fail/60 text-fail",
} as const;

/** A Winner Score in its band colour, or a dash before the first result. */
export function ScoreChip({ score }: { score: number | null | undefined }) {
  if (score === null || score === undefined) {
    return (
      <span className="text-muted">
        <span aria-hidden="true">—</span>
        <span className="sr-only">No score yet</span>
      </span>
    );
  }
  return (
    <span
      className={cn(
        "num inline-flex h-6 min-w-9 items-center justify-center rounded-sm border px-1.5 text-13 font-medium",
        SCORE_CHIP[scoreBand(score)],
      )}
    >
      {score}
    </span>
  );
}
