import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Report } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";

import { CreativeTile } from "./CreativeTile";
import { makeAd, makeAdvertiser, makeReport } from "./fixtures";
import { groupCreatives } from "./gallery";
import { renderReport, reportGets, serveReport } from "./testing";

afterEach(() => vi.unstubAllGlobals());

function tiles() {
  return screen.getAllByTestId("creative-tile");
}

describe("CreativeGallery", () => {
  it("sorts longest running first", async () => {
    serveReport(() =>
      makeReport({
        ads: [
          makeAd({ ad_archive_id: "1", days_running: 40, start_date: "2026-09-07T00:00:00Z" }),
          makeAd({ ad_archive_id: "2", days_running: 230, start_date: "2026-03-01T00:00:00Z" }),
          makeAd({ ad_archive_id: "3", days_running: 95, start_date: "2026-07-14T00:00:00Z" }),
        ],
        advertisers: [makeAdvertiser()],
      }),
    );
    renderReport();

    expect(await screen.findByRole("tab", { name: "Creatives (3)" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(tiles().map((t) => within(t).getByTestId("tile-days").textContent)).toEqual([
      "230",
      "95",
      "40",
    ]);
    expect(screen.getByRole("combobox", { name: "Sort" })).toHaveTextContent("Longest running");
  });

  it("missing media shows placeholder", () => {
    const [noMedia, broken] = groupCreatives([
      makeAd({ ad_archive_id: "1", media: [] }),
      makeAd({
        ad_archive_id: "2",
        media: [{ type: "image", url: "https://cdn.test/x.jpg", local_path: "1/2_0.jpg" }],
      }),
    ]);
    const { rerender } = render(
      <TooltipProvider>
        <CreativeTile
          group={noMedia!}
          mode="relevant"
          timezone="UTC"
          onToggle={vi.fn()}
          pending={false}
        />
      </TooltipProvider>,
    );
    expect(screen.getByText("Media unavailable")).toBeInTheDocument();

    rerender(
      <TooltipProvider>
        <CreativeTile
          group={broken!}
          mode="relevant"
          timezone="UTC"
          onToggle={vi.fn()}
          pending={false}
        />
      </TooltipProvider>,
    );
    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("src", "/media/1/2_0.jpg");
    fireEvent.error(img);
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://cdn.test/x.jpg");
    fireEvent.error(screen.getByRole("img"));
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("Media unavailable")).toBeInTheDocument();
  });

  it("variants badge counts grouped ads", async () => {
    serveReport(() =>
      makeReport({
        ads: [
          makeAd({ ad_archive_id: "1", creative_key: "shared", days_running: 120 }),
          makeAd({ ad_archive_id: "2", creative_key: "shared", days_running: 230 }),
          makeAd({ ad_archive_id: "3", creative_key: "shared", days_running: 12 }),
          makeAd({ ad_archive_id: "4", creative_key: "solo", days_running: 60 }),
        ],
      }),
    );
    renderReport();

    expect(await screen.findByRole("tab", { name: "Creatives (2)" })).toBeInTheDocument();
    const [shared, solo] = tiles();
    expect(within(shared!).getByTestId("variants-badge").textContent).toBe("+2 variants");
    expect(within(shared!).getByTestId("ad-count").textContent).toBe("3 ads use this");
    expect(within(shared!).getByTestId("tile-days").textContent).toBe("230");
    expect(within(solo!).queryByTestId("variants-badge")).toBeNull();
    expect(within(solo!).queryByTestId("ad-count")).toBeNull();
  });

  it("exclude ad calls override and refreshes", async () => {
    const before = makeReport({
      ads: [makeAd({ ad_archive_id: "7" }), makeAd({ ad_archive_id: "8", days_running: 30 })],
    });
    const after = makeReport({
      ads: [
        makeAd({ ad_archive_id: "7", relevant: false, reason: "ad_override" }),
        makeAd({ ad_archive_id: "8", days_running: 30 }),
      ],
    });
    let current: Report = before;
    const calls = serveReport(() => current);
    const user = userEvent.setup();
    renderReport();

    await user.click(await screen.findByRole("tab", { name: "Creatives (2)" }));
    const first = tiles()[0]!;
    current = after;
    await user.click(within(first).getByRole("button", { name: /^Exclude/ }));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        snapshot_id: 1,
        scope: "ad",
        target_id: "7",
        mode: "exclude",
      }),
    );
    expect(await screen.findByRole("tab", { name: "Creatives (1)" })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByRole("tab", { name: "Excluded (1)" })).toBeInTheDocument();
    expect(reportGets(calls)).toBe(2);
    expect(await screen.findByText("Excluded ad from Northbrace")).toBeInTheDocument();
  });

  it("include on a keyword-filtered ad forces include; on a user exclusion clears it", async () => {
    const report = makeReport({
      ads: [
        makeAd({ ad_archive_id: "1", relevant: false, reason: "no_keyword", days_running: 90 }),
        makeAd({ ad_archive_id: "2", relevant: false, reason: "ad_override", days_running: 50 }),
      ],
    });
    const calls = serveReport(() => report);
    const user = userEvent.setup();
    renderReport();

    await user.click(await screen.findByRole("tab", { name: "Excluded (2)" }));
    const [keywordTile, userTile] = tiles();
    expect(within(keywordTile!).getByText("No keyword match")).toBeInTheDocument();
    expect(within(userTile!).getByText("You excluded this ad")).toBeInTheDocument();

    await user.click(within(keywordTile!).getByRole("button", { name: /^Include/ }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        snapshot_id: 1,
        scope: "ad",
        target_id: "1",
        mode: "include",
      }),
    );
    await user.click(within(userTile!).getByRole("button", { name: /^Include/ }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({
        snapshot_id: 1,
        scope: "ad",
        target_id: "2",
      }),
    );
    expect(screen.getByRole("tab", { name: "Excluded (2)" })).toHaveAttribute(
      "data-state",
      "active",
    );
  });

  it("a group's overrides refresh the report once", async () => {
    const group = ["1", "2", "3", "4"].map((id) =>
      makeAd({ ad_archive_id: id, creative_key: "shared" }),
    );
    const calls = serveReport(() => makeReport({ ads: group }));
    const user = userEvent.setup();
    renderReport();

    await screen.findByRole("tab", { name: "Creatives (1)" });
    await user.click(within(tiles()[0]!).getByRole("button", { name: /^Exclude/ }));

    expect(await screen.findByText("Excluded 4 ads from Northbrace")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(4);
    expect(reportGets(calls)).toBe(2);
  });

  it("a failure part-way still refreshes and says how far it got", async () => {
    const group = ["1", "2", "3", "4"].map((id) =>
      makeAd({ ad_archive_id: id, creative_key: "shared" }),
    );
    const calls = serveReport(() => makeReport({ ads: group }), {
      at: 3,
      detail: "Database is locked.",
    });
    const user = userEvent.setup();
    renderReport();

    await screen.findByRole("tab", { name: "Creatives (1)" });
    await user.click(within(tiles()[0]!).getByRole("button", { name: /^Exclude/ }));

    expect(await screen.findByText("Excluded 2 of 4 ads. Database is locked.")).toBeInTheDocument();
    // The 4th call is never sent once the 3rd fails.
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(3);
    await waitFor(() => expect(reportGets(calls)).toBe(2));
    expect(screen.getByRole("button", { name: /^Exclude/ })).toBeEnabled();
  });

  it("a mixed-reason group shows every reason and names an advertiser-wide include", async () => {
    const calls = serveReport(() =>
      makeReport({
        ads: [
          makeAd({
            ad_archive_id: "1",
            creative_key: "merged",
            relevant: false,
            reason: "advertiser_override",
          }),
          ...["2", "3"].map((id) =>
            makeAd({
              ad_archive_id: id,
              creative_key: "merged",
              page_id: "998",
              advertiser_key: "998",
              page_name: "Upright Co",
              relevant: false,
              reason: "no_keyword",
              days_running: 10,
            }),
          ),
        ],
      }),
    );
    const user = userEvent.setup();
    renderReport();

    await user.click(await screen.findByRole("tab", { name: "Excluded (1)" }));
    const tile = tiles()[0]!;
    expect(within(tile).getByText("You excluded this advertiser")).toBeInTheDocument();
    expect(within(tile).getByText("No keyword match")).toBeInTheDocument();

    await user.click(
      within(tile).getByRole("button", { name: "Include 2 ads and all ads from Northbrace" }),
    );
    expect(
      await screen.findByText("Included 2 ads and all ads from Northbrace"),
    ).toBeInTheDocument();
    expect(calls.filter((c) => c.method !== "GET").map((c) => [c.method, c.body])).toEqual([
      ["DELETE", { snapshot_id: 1, scope: "advertiser", target_id: "514393445092458" }],
      ["PUT", { snapshot_id: 1, scope: "ad", target_id: "2", mode: "include" }],
      ["PUT", { snapshot_id: 1, scope: "ad", target_id: "3", mode: "include" }],
    ]);
    expect(reportGets(calls)).toBe(2);
  });

  it("a double click before the re-render runs the toggle once", async () => {
    const calls = serveReport(() => makeReport({ ads: [makeAd({ ad_archive_id: "7" })] }));
    renderReport();

    const [tile] = await screen.findAllByTestId("creative-tile");
    const button = within(tile!).getByRole("button", { name: /^Exclude/ });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(await screen.findByText("Excluded ad from Northbrace")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);
  });

  it("filters persist across tabs", async () => {
    const video = {
      type: "video" as const,
      url: "https://cdn.test/v.mp4",
      preview_url: null,
      local_path: null,
      poster_path: null,
      phash: null,
    };
    serveReport(() =>
      makeReport({
        ads: [
          makeAd({ ad_archive_id: "1", creative_key: "t:1", days_running: 40 }),
          makeAd({ ad_archive_id: "2", creative_key: "t:2", days_running: 90, media: [video] }),
          makeAd({
            ad_archive_id: "3",
            creative_key: "t:3",
            relevant: false,
            reason: "no_keyword",
          }),
        ],
        advertisers: [makeAdvertiser()],
      }),
    );
    renderReport();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("radio", { name: "Videos" }));
    expect(tiles()).toHaveLength(1);

    await user.click(screen.getByRole("tab", { name: /^Advertisers/ }));
    await user.click(screen.getByRole("tab", { name: /^Excluded/ }));
    // Each tab keeps its own filters: the Excluded gallery is unfiltered.
    expect(screen.getByRole("radio", { name: "All media" })).toHaveAttribute("data-state", "on");
    await user.click(screen.getByRole("tab", { name: /^Creatives/ }));

    expect(screen.getByRole("radio", { name: "Videos" })).toHaveAttribute("data-state", "on");
    expect(tiles()).toHaveLength(1);
    expect(screen.getByText(/creatives$/, { selector: "p[aria-live]" })).toHaveTextContent(
      "1 of 2 creatives",
    );
  });
});
