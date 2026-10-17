/**
 * Display formatting. The API sends ISO-8601 UTC instants; the UI shows them in
 * the timezone from GET /api/settings (default Australia/Melbourne).
 */

export const DEFAULT_TIMEZONE = "Australia/Melbourne";

// Fixed month names: some ICU builds write "Sept", which breaks column alignment.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const partsFormatters = new Map<string, Intl.DateTimeFormat>();

function partsFormatter(timeZone: string): Intl.DateTimeFormat {
  let formatter = partsFormatters.get(timeZone);
  if (formatter === undefined) {
    formatter = new Intl.DateTimeFormat("en-AU", {
      timeZone,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    partsFormatters.set(timeZone, formatter);
  }
  return formatter;
}

interface LocalParts {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
}

function localParts(iso: string, timeZone: string): LocalParts {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    throw new RangeError(`Not an ISO date: ${iso}`);
  }
  const parts: Record<string, string> = {};
  for (const part of partsFormatter(timeZone).formatToParts(date)) {
    parts[part.type] = part.value;
  }
  const monthIndex = Number(parts.month) - 1;
  return {
    year: parts.year ?? "",
    month: MONTHS[monthIndex] ?? "",
    day: String(Number(parts.day)),
    hour: parts.hour ?? "",
    minute: parts.minute ?? "",
  };
}

/** `17 Oct 2026`, the calendar date of `iso` in `timeZone`. */
export function formatDate(iso: string, timeZone: string = DEFAULT_TIMEZONE): string {
  const p = localParts(iso, timeZone);
  return `${p.day} ${p.month} ${p.year}`;
}

/** `17 Oct 2026 01:30`, with a 24-hour local time. */
export function formatDateTime(iso: string, timeZone: string = DEFAULT_TIMEZONE): string {
  const p = localParts(iso, timeZone);
  return `${p.day} ${p.month} ${p.year} ${p.hour}:${p.minute}`;
}

const intFormatter = new Intl.NumberFormat("en-AU", { maximumFractionDigits: 0 });

/** `1,234`, rounded to a whole number. */
export function formatInt(n: number): string {
  return intFormatter.format(Math.round(n));
}

/** `1 day`, `42 days`. */
export function formatDays(n: number): string {
  const rounded = Math.round(n);
  return `${formatInt(rounded)} ${rounded === 1 ? "day" : "days"}`;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * `Just now`, `3 min ago`, `5 h ago`, `2 days ago`; the calendar date after 30
 * days. Pair it with `formatDateTime` in a tooltip for the exact time.
 */
export function formatRelative(
  iso: string,
  now: Date = new Date(),
  timeZone: string = DEFAULT_TIMEZONE,
): string {
  const elapsed = now.getTime() - new Date(iso).getTime();
  if (elapsed < MINUTE) return "Just now";
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)} min ago`;
  if (elapsed < DAY) return `${Math.floor(elapsed / HOUR)} h ago`;
  const days = Math.floor(elapsed / DAY);
  if (days <= 30) return `${days} ${days === 1 ? "day" : "days"} ago`;
  return formatDate(iso, timeZone);
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** `Sun 09:00`: the local weekday and 24-hour time of `iso`, for the next daily check. */
export function formatWeekdayTime(iso: string, timeZone: string = DEFAULT_TIMEZONE): string {
  const p = localParts(iso, timeZone);
  const monthIndex = MONTHS.indexOf(p.month);
  // The local calendar date's weekday; noon UTC keeps it clear of any offset.
  const weekday = new Date(Date.UTC(Number(p.year), monthIndex, Number(p.day), 12)).getUTCDay();
  return `${WEEKDAYS[weekday] ?? ""} ${p.hour}:${p.minute}`;
}

/** `15:01`: the 24-hour local time of `iso`. */
export function formatTime(iso: string, timeZone: string = DEFAULT_TIMEZONE): string {
  const p = localParts(iso, timeZone);
  return `${p.hour}:${p.minute}`;
}

/** `17 Oct`, from a calendar date (`2026-10-17`) that is already local. */
export function formatDayMonth(isoDate: string): string {
  const [, month, day] = isoDate.split("-");
  return `${Number(day)} ${MONTHS[Number(month) - 1] ?? ""}`;
}

/** `+12%`, `-30%`, `0%`: the change from `prev` to `now`; null when `prev` is 0. */
export function formatChange(prev: number, now: number): string | null {
  if (prev === 0) return null;
  const pct = Math.round(((now - prev) / prev) * 100);
  return `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct)}%`;
}
