import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Report } from "@/api/client";

import { AdvertiserTable } from "./AdvertiserTable";
import { makeAd, makeAdvertiser, makeReport } from "./fixtures";
import { renderReport, serveReport } from "./testing";

afterEach(() => vi.unstubAllGlobals());

const ROWS = [
  makeAdvertiser(),
  makeAdvertiser({
    key: "998",
    name: "Upright Co",
    ads: 12,
    total_ads: 14,
    creatives: 9,
    days_running_max: 410,
    landing_domain: "upright.co",
    is_leader: false,
  }),
  makeAdvertiser({
    key: "javascript",
    name: "Odd Page",
    ads: 1,
    total_ads: 1,
    creatives: 1,
    days_running_max: 3,
    landing_domain: "javascript:alert(1)",
    is_leader: false,
  }),
];

describe("AdvertiserTable", () => {
  it("landing link is safe", () => {
    render(<AdvertiserTable rows={ROWS} onToggle={vi.fn()} pendingKey={null} />);

    const link = screen.getByRole("link", { name: "northbrace.test" });
    expect(link).toHaveAttribute("href", "https://northbrace.test/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    // Anything that isn't a plain hostname is shown, never linked.
    expect(screen.queryByRole("link", { name: /javascript/ })).toBeNull();
    expect(screen.getByText("javascript:alert(1)")).toBeInTheDocument();
  });

  it("marks the leader and sorts by a column on click", async () => {
    const user = userEvent.setup();
    render(<AdvertiserTable rows={ROWS} onToggle={vi.fn()} pendingKey={null} />);

    const names = () =>
      screen
        .getAllByRole("row")
        .slice(1)
        .map((row) => within(row).getAllByRole("cell")[0]!.textContent);
    expect(names()[0]).toContain("Northbrace");
    expect(within(screen.getAllByRole("row")[1]!).getByText("Leader")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Oldest ad (days)" }));
    expect(names()[0]).toContain("Upright Co");
    expect(screen.getByRole("columnheader", { name: "Oldest ad (days)" })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    await user.click(screen.getByRole("button", { name: "Oldest ad (days)" }));
    expect(names()[0]).toContain("Odd Page");
  });

  it("advertiser exclude moves its ads to excluded tab", async () => {
    const before = makeReport({
      ads: [
        makeAd({ ad_archive_id: "1" }),
        makeAd({
          ad_archive_id: "2",
          page_id: "998",
          advertiser_key: "998",
          page_name: "Upright Co",
        }),
      ],
      advertisers: ROWS.slice(0, 2),
    });
    const after = makeReport({
      ads: [
        makeAd({ ad_archive_id: "1", relevant: false, reason: "advertiser_override" }),
        makeAd({
          ad_archive_id: "2",
          page_id: "998",
          advertiser_key: "998",
          page_name: "Upright Co",
        }),
      ],
      advertisers: [
        makeAdvertiser({ ads: 0, creatives: 0, excluded: true, is_leader: false }),
        ROWS[1]!,
      ],
    });
    let current: Report = before;
    const calls = serveReport(() => current);
    const user = userEvent.setup();
    renderReport();

    await user.click(await screen.findByRole("tab", { name: "Advertisers (2)" }));
    const table = screen.getByRole("table", { name: /^Advertisers/ });
    const northbrace = within(table).getAllByRole("row")[1]!;
    current = after;
    await user.click(within(northbrace).getByRole("button", { name: /^Exclude/ }));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        snapshot_id: 1,
        scope: "advertiser",
        target_id: "514393445092458",
        mode: "exclude",
      }),
    );
    expect(await screen.findByText("Excluded Northbrace")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Advertisers (2)" })).toHaveAttribute(
      "data-state",
      "active",
    );

    await user.click(screen.getByRole("tab", { name: "Excluded (1)" }));
    const [tile] = screen.getAllByTestId("creative-tile");
    expect(within(tile!).getByText("You excluded this advertiser")).toBeInTheDocument();
    expect(within(tile!).getByText("Northbrace")).toBeInTheDocument();

    await user.click(within(tile!).getByRole("button", { name: /^Include/ }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({
        snapshot_id: 1,
        scope: "advertiser",
        target_id: "514393445092458",
      }),
    );
  });
});
