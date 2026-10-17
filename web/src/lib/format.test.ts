import { describe, expect, it } from "vitest";

import {
  formatChange,
  formatDate,
  formatDateTime,
  formatDayMonth,
  formatDays,
  formatInt,
  formatRelative,
  formatTime,
  formatWeekdayTime,
} from "./format";

const MELBOURNE = "Australia/Melbourne";

describe("formatDate", () => {
  it("formats a UTC instant as the calendar date in Melbourne", () => {
    // 14:30 UTC on 16 Oct is 01:30 AEDT on 17 Oct.
    expect(formatDate("2026-10-16T14:30:00Z", MELBOURNE)).toBe("17 Oct 2026");
  });

  it("keeps the UTC date when the timezone is UTC", () => {
    expect(formatDate("2026-10-16T14:30:00Z", "UTC")).toBe("16 Oct 2026");
  });

  it("accepts ISO strings with an explicit offset", () => {
    expect(formatDate("2026-07-01T00:00:00+00:00", MELBOURNE)).toBe("1 Jul 2026");
  });
});

describe("formatDateTime", () => {
  it("includes the local 24-hour time", () => {
    expect(formatDateTime("2026-10-16T14:30:00Z", MELBOURNE)).toBe("17 Oct 2026 01:30");
  });
});

describe("formatDays", () => {
  it("uses the singular for one day", () => {
    expect(formatDays(1)).toBe("1 day");
  });

  it("uses the plural otherwise", () => {
    expect(formatDays(0)).toBe("0 days");
    expect(formatDays(42)).toBe("42 days");
  });

  it("groups thousands", () => {
    expect(formatDays(1200)).toBe("1,200 days");
  });
});

describe("formatInt", () => {
  it("groups thousands and rounds", () => {
    expect(formatInt(1234567)).toBe("1,234,567");
    expect(formatInt(12.6)).toBe("13");
  });
});

describe("formatRelative", () => {
  const now = new Date("2026-10-17T10:00:00Z");

  it("says just now under a minute", () => {
    expect(formatRelative("2026-10-17T09:59:30Z", now)).toBe("Just now");
  });

  it("uses minutes, hours and days", () => {
    expect(formatRelative("2026-10-17T09:57:00Z", now)).toBe("3 min ago");
    expect(formatRelative("2026-10-17T07:00:00Z", now)).toBe("3 h ago");
    expect(formatRelative("2026-10-16T09:00:00Z", now)).toBe("1 day ago");
    expect(formatRelative("2026-10-12T09:00:00Z", now)).toBe("5 days ago");
  });

  it("falls back to the date after a month", () => {
    expect(formatRelative("2026-08-01T09:00:00Z", now, "UTC")).toBe("1 Aug 2026");
  });
});

describe("formatWeekdayTime", () => {
  it("shows the local weekday and time", () => {
    // 22:00 UTC on Sat 17 Oct is 09:00 AEDT on Sun 18 Oct.
    expect(formatWeekdayTime("2026-10-17T22:00:00Z", MELBOURNE)).toBe("Sun 09:00");
    expect(formatWeekdayTime("2026-10-17T22:00:00Z", "UTC")).toBe("Sat 22:00");
  });
});

describe("formatDayMonth", () => {
  it("formats a calendar date without a timezone shift", () => {
    expect(formatDayMonth("2026-10-07")).toBe("7 Oct");
  });
});

describe("formatTime", () => {
  it("gives the 24-hour local time", () => {
    expect(formatTime("2026-10-17T12:23:33Z", "Australia/Melbourne")).toBe("23:23");
    expect(formatTime("2026-10-17T04:01:33Z", "UTC")).toBe("04:01");
  });
});

describe("formatChange", () => {
  it("signs the percentage and skips a zero base", () => {
    expect(formatChange(40, 52)).toBe("+30%");
    expect(formatChange(40, 30)).toBe("−25%");
    expect(formatChange(40, 40)).toBe("0%");
    expect(formatChange(0, 5)).toBeNull();
  });
});
