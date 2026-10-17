import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makeBucket } from "./fixtures";
import { LaunchTimeline } from "./LaunchTimeline";
import { axisTicks, formatBucketRange, toSeries } from "./timeline";

describe("LaunchTimeline", () => {
  it("timeline splits leader launches", () => {
    const rows = toSeries(
      [
        makeBucket({ start: "2026-07-01", new_creatives: 4, ads: 12, leader_new_creatives: 2 }),
        makeBucket({ start: "2026-08-01", new_creatives: 20, ads: 31, leader_new_creatives: 16 }),
      ],
      "month",
    );

    expect(rows).toEqual([
      { start: "2026-07-01", end: "2026-07-31", leader: 2, others: 2, total: 4, ads: 12 },
      { start: "2026-08-01", end: "2026-08-31", leader: 16, others: 4, total: 20, ads: 31 },
    ]);

    render(
      <LaunchTimeline
        month={[makeBucket({ new_creatives: 20, ads: 31, leader_new_creatives: 16 })]}
        week={[makeBucket({ start: "2026-08-10", new_creatives: 11, leader_new_creatives: 10 })]}
        leaderName="Northbrace"
      />,
    );
    // The legend names both series, and the table view carries the split.
    expect(screen.getByText("Northbrace", { selector: "li *" })).toBeInTheDocument();
    expect(screen.getByText("Other advertisers", { selector: "li *" })).toBeInTheDocument();
    const table = screen.getByRole("table", { name: /new creatives per month/i });
    expect(table).toHaveTextContent("1–31 Aug 2026");
    expect(table).toHaveTextContent("20164");
  });

  it("keeps the aria-hidden chart out of the tab order", () => {
    const { container } = render(
      <LaunchTimeline
        month={[makeBucket({ new_creatives: 3, ads: 3, leader_new_creatives: 1 })]}
        week={[makeBucket({ new_creatives: 3, ads: 3, leader_new_creatives: 1 })]}
        leaderName="Northbrace"
      />,
    );
    const hidden = container.querySelector('[aria-hidden="true"] .recharts-wrapper');
    expect(hidden).not.toBeNull();
    expect(hidden?.querySelector('[tabindex]:not([tabindex="-1"])')).toBeNull();
  });

  it("switches to weekly buckets", async () => {
    render(
      <LaunchTimeline
        month={[makeBucket({ new_creatives: 20, ads: 31, leader_new_creatives: 16 })]}
        week={[makeBucket({ start: "2026-08-10", new_creatives: 11, leader_new_creatives: 10 })]}
        leaderName="Northbrace"
      />,
    );
    await userEvent.click(screen.getByRole("radio", { name: "Week" }));
    expect(screen.getByRole("table", { name: /new creatives per week/i })).toHaveTextContent(
      "10–16 Aug 2026",
    );
  });

  it("shows a line instead of an empty chart", () => {
    render(<LaunchTimeline month={[makeBucket()]} week={[]} leaderName="Northbrace" />);
    expect(screen.getByText("No launch dates yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("formats bucket ranges and marks year boundaries", () => {
    expect(formatBucketRange("2026-07-27", "2026-08-02")).toBe("27 Jul – 2 Aug 2026");
    expect(formatBucketRange("2025-12-29", "2026-01-04")).toBe("29 Dec 2025 – 4 Jan 2026");

    const months = ["2024-11", "2024-12", "2025-01", "2025-02", "2025-03", "2025-04"].map((m) =>
      makeBucket({ start: `${m}-01` }),
    );
    expect(axisTicks(toSeries(months, "month"))).toEqual([
      { start: "2025-01-01", label: "2025", year: true },
      { start: "2025-04-01", label: "Apr", year: false },
    ]);
  });
});
