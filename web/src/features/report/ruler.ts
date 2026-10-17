import type { Schemas } from "@/api/client";

type FactorResult = Schemas["FactorResult"];
export type FactorStatus = FactorResult["status"];

/**
 * The right end of a factor's track: a quarter past `full`, or half past `pass`,
 * whichever is further. A fixed range keeps the thresholds legible; a value
 * beyond it pins to the end (see `rulerOverflows`) instead of squeezing them.
 */
export function rulerMax(factor: FactorResult): number {
  const { full, pass } = factor.threshold;
  return Math.max((full ?? 0) * 1.25, pass * 1.5);
}

/** The value runs past the track's end: the marker pins there under an overflow arrow. */
export function rulerOverflows(factor: FactorResult): boolean {
  return factor.value !== null && factor.value > rulerMax(factor);
}

/** `value` as a CSS percentage of `max`, clamped to the track. */
export function percent(value: number, max: number): string {
  const share = max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0;
  return `${Number((share * 100).toFixed(3))}%`;
}

/** `30`, `7.5`: thresholds and points without trailing zeros. */
export function formatAmount(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Number(n.toFixed(1)));
}

export interface RulerTick {
  kind: "fail" | "pass" | "full";
  at: number;
  /** The words before the number, e.g. `fail <`. */
  prefix: string;
}

export function rulerTicks(factor: FactorResult): RulerTick[] {
  const { fail, pass, full } = factor.threshold;
  const ticks: RulerTick[] = [
    { kind: "fail", at: fail, prefix: "fail <" },
    { kind: "pass", at: pass, prefix: "pass ≥" },
  ];
  if (full !== null && full !== undefined) ticks.push({ kind: "full", at: full, prefix: "full" });
  return ticks;
}

/**
 * Left edges (px) for labels that want to start at `desired`, nudged right so
 * none overlap, then pulled back so the last one stays inside `container`.
 */
export function layoutLabels(
  desired: number[],
  widths: number[],
  container: number,
  gap: number,
): number[] {
  const lefts: number[] = [];
  let edge = -Infinity;
  desired.forEach((want, i) => {
    const left = Math.max(want, edge + gap, 0);
    lefts.push(left);
    edge = left + (widths[i] ?? 0);
  });
  let limit = container;
  for (let i = lefts.length - 1; i >= 0; i--) {
    const width = widths[i] ?? 0;
    const left = Math.max(Math.min(lefts[i] ?? 0, limit - width), 0);
    lefts[i] = left;
    limit = left - gap;
  }
  return lefts;
}

/**
 * The track's threshold bands: below fail, between fail and pass, and at or
 * past pass. Widths are CSS percentages of the track.
 */
export function rulerZones(
  factor: FactorResult,
  max: number,
): { status: "fail" | "warn" | "pass"; width: string }[] {
  const { fail, pass } = factor.threshold;
  const failEnd = Math.min(fail, max);
  const passStart = Math.min(Math.max(pass, failEnd), max);
  return [
    { status: "fail", width: percent(failEnd, max) },
    { status: "warn", width: percent(passStart - failEnd, max) },
    { status: "pass", width: percent(max - passStart, max) },
  ];
}

/** Zone tints: quiet enough that the pointer stays the loudest mark on the track. */
export const ZONE_BG = {
  fail: "bg-fail/28",
  warn: "bg-warn/28",
  pass: "bg-pass/28",
} as const;

export const STATUS_TEXT: Record<FactorStatus, string> = {
  pass: "text-pass",
  warn: "text-warn",
  fail: "text-fail",
  pending: "text-muted",
};

/** Winner Score band: ≥ 70 pass, 40–69 warn, < 40 fail. */
export function scoreBand(score: number): "pass" | "warn" | "fail" {
  if (score >= 70) return "pass";
  if (score >= 40) return "warn";
  return "fail";
}
