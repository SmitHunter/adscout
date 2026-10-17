import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeAdvertiser, makeReport } from "@/features/report/fixtures";
import { ReportPage } from "@/features/report/ReportPage";

import { makeWatchItem } from "./fixtures";
import { renderRoutes, serveWatch, stubResizeObserver, writes } from "./testing";

beforeEach(stubResizeObserver);
afterEach(() => vi.unstubAllGlobals());

const renderReport = () =>
  renderRoutes([{ path: "/report/:id", element: <ReportPage /> }], "/report/1");

describe("WatchButton", () => {
  it("watch button toggles on report", async () => {
    const { calls } = serveWatch({
      report: makeReport(),
      // Same keyword, another country: not this report's search.
      items: [makeWatchItem({ id: 7, country: "AU" })],
    });
    renderReport();
    const user = userEvent.setup();

    const watch = await screen.findByRole("button", { name: "Watch" });
    expect(watch).toHaveAttribute("aria-pressed", "false");
    await user.click(watch);

    const watching = await screen.findByRole("button", { name: "Watching" });
    expect(watching).toHaveAttribute("aria-pressed", "true");
    expect(writes(calls)).toEqual([
      {
        method: "POST",
        url: "/api/watch",
        body: {
          kind: "keyword",
          keyword: "posture corrector",
          page_id: null,
          country: "US",
          active_status: "active",
          media_type: "all",
          limit: 300,
        },
      },
    ]);

    await user.click(watching);
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Stop watching" }));
    expect(await screen.findByRole("button", { name: "Watch" })).toBeInTheDocument();
    expect(writes(calls).at(-1)).toEqual({
      method: "DELETE",
      url: "/api/watch/100",
      body: undefined,
    });
  });

  it("a paused watch says so and links to its trends", async () => {
    serveWatch({
      report: makeReport(),
      items: [makeWatchItem({ id: 7, country: "US", enabled: false })],
    });
    renderReport();

    const watching = await screen.findByRole("button", { name: "Watching" });
    expect(watching).toHaveAttribute("aria-pressed", "true");
    const paused = screen.getByRole("link", { name: "Paused" });
    expect(paused).toHaveAttribute("href", "/watch/7");
  });

  it("an enabled watch shows no paused note", async () => {
    serveWatch({ report: makeReport(), items: [makeWatchItem({ id: 7, country: "US" })] });
    renderReport();
    await screen.findByRole("button", { name: "Watching" });
    expect(screen.queryByRole("link", { name: "Paused" })).not.toBeInTheDocument();
  });

  it("advertiser watch from table", async () => {
    const { calls } = serveWatch({
      report: makeReport({
        advertisers: [
          makeAdvertiser(),
          makeAdvertiser({ key: "998877", name: "Postura", is_leader: false }),
        ],
      }),
      items: [
        makeWatchItem({
          id: 4,
          kind: "advertiser",
          query: "Postura",
          page_id: "998877",
          country: "US",
        }),
      ],
    });
    renderReport();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("tab", { name: /Advertisers/ }));
    expect(await screen.findByRole("button", { name: "Watching Postura" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Watch Northbrace" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Watching Northbrace" })).toBeInTheDocument(),
    );
    expect(writes(calls)).toEqual([
      {
        method: "POST",
        url: "/api/watch",
        body: {
          kind: "advertiser",
          keyword: "Northbrace",
          page_id: "514393445092458",
          country: "US",
          active_status: "active",
          media_type: "all",
          limit: 300,
        },
      },
    ]);
    expect(await screen.findByText("Watching Northbrace daily.")).toBeInTheDocument();
  });
});
