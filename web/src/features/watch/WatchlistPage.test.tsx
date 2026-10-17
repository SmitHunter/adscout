import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeWatchItem } from "./fixtures";
import { renderRoutes, serveWatch, stubResizeObserver, writes } from "./testing";
import { WatchlistPage } from "./WatchlistPage";

beforeEach(stubResizeObserver);
afterEach(() => vi.unstubAllGlobals());

const renderWatchlist = () =>
  renderRoutes(
    [
      { path: "/watch", element: <WatchlistPage /> },
      { path: "/watch/:id", element: <p>trend page</p> },
    ],
    "/watch",
  );

function row(name: string): HTMLElement {
  const cell = screen.getByRole("link", { name });
  const tr = cell.closest("tr");
  if (tr === null) throw new Error(`No row for ${name}`);
  return tr;
}

describe("WatchlistPage", () => {
  it("watchlist renders sparkline and next run", async () => {
    serveWatch({
      items: [
        makeWatchItem(),
        makeWatchItem({
          id: 2,
          kind: "advertiser",
          query: "Northbrace",
          page_id: "514393445092458",
          country: "ALL",
          enabled: false,
          next_run_at: null,
          last_status: "capped",
          sparkline: [300, 300],
          latest_point: null,
        }),
      ],
    });
    renderWatchlist();

    // 22:00 UTC on Sat 17 Oct is 09:00 on Sun 18 Oct in Melbourne.
    expect(
      await screen.findByText(
        (_, el) => el?.tagName === "P" && el.textContent === "Next check Sun 09:00",
      ),
    ).toBeInTheDocument();

    const product = row("posture corrector");
    expect(within(product).getByText("Product")).toBeInTheDocument();
    const spark = within(product).getByRole("img", { name: /Active ads over the last 14 checks/ });
    expect(spark).toHaveAttribute("data-points", "14");
    expect(within(product).getByText("182")).toBeInTheDocument();
    expect(within(product).getByText("78")).toBeInTheDocument();
    expect(
      within(product).getByRole("switch", { name: "Check posture corrector daily" }),
    ).toBeChecked();

    const advertiser = row("Northbrace");
    expect(within(advertiser).getByText("Advertiser")).toBeInTheDocument();
    expect(within(advertiser).getByText("Capped")).toBeInTheDocument();
    expect(within(advertiser).getByRole("switch")).not.toBeChecked();
  });

  it("shows the empty state", async () => {
    serveWatch();
    renderWatchlist();
    expect(
      await screen.findByText("Watch a product or advertiser to track it daily."),
    ).toBeInTheDocument();
  });

  it("add watch posts and lists", async () => {
    const { calls } = serveWatch({ items: [makeWatchItem()] });
    renderWatchlist();
    const user = userEvent.setup();

    await screen.findByRole("link", { name: "posture corrector" });
    await user.type(screen.getByRole("textbox", { name: "Product keyword" }), "neck fan");
    await user.click(screen.getByRole("radio", { name: "AU" }));
    await user.click(screen.getByRole("button", { name: "Watch" }));

    expect(await screen.findByRole("link", { name: "neck fan" })).toBeInTheDocument();
    expect(writes(calls)).toEqual([
      {
        method: "POST",
        url: "/api/watch",
        body: {
          kind: "keyword",
          keyword: "neck fan",
          page_id: null,
          country: "AU",
          active_status: "active",
          media_type: "all",
          limit: 300,
        },
      },
    ]);
    expect(screen.getByRole("textbox", { name: "Product keyword" })).toHaveValue("");

    // The same search again: the API's 409 shows next to the form.
    await user.type(screen.getByRole("textbox", { name: "Product keyword" }), "neck fan");
    await user.click(screen.getByRole("button", { name: "Watch" }));
    expect(await screen.findByText("Already watching this.")).toBeInTheDocument();
  });

  it("remove confirms", async () => {
    const { calls } = serveWatch({ items: [makeWatchItem()] });
    renderWatchlist();
    const user = userEvent.setup();

    await screen.findByRole("link", { name: "posture corrector" });
    await user.click(screen.getByRole("button", { name: "Remove posture corrector" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Stop watching posture corrector?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(writes(calls)).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Remove posture corrector" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "posture corrector" })).toBeNull(),
    );
    expect(writes(calls)).toEqual([{ method: "DELETE", url: "/api/watch/1", body: undefined }]);
    expect(
      await screen.findByText("Watch a product or advertiser to track it daily."),
    ).toBeInTheDocument();
  });

  it("remove dialog takes focus, closes on Escape and gives focus back", async () => {
    const { calls } = serveWatch({ items: [makeWatchItem()] });
    renderWatchlist();
    const user = userEvent.setup();

    const trigger = await screen.findByRole("button", { name: "Remove posture corrector" });
    await user.click(trigger);
    const dialog = await screen.findByRole("alertdialog");
    // The safe choice has focus first.
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(trigger).toHaveFocus();
    expect(writes(calls)).toEqual([]);
  });

  it("remove is disabled while the item is being checked", async () => {
    serveWatch({ items: [makeWatchItem({ running: true })] });
    renderWatchlist();
    expect(await screen.findByRole("button", { name: "Remove posture corrector" })).toBeDisabled();
  });

  it("an advertiser watched in every country says so", async () => {
    serveWatch({
      items: [makeWatchItem({ id: 2, kind: "advertiser", query: "Northbrace", country: "ALL" })],
    });
    renderWatchlist();
    await screen.findByRole("link", { name: "Northbrace" });
    expect(within(row("Northbrace")).getByText("All countries")).toBeInTheDocument();
  });

  it("run now toasts copy", async () => {
    const { calls } = serveWatch({ items: [makeWatchItem()] });
    renderWatchlist();
    const user = userEvent.setup();

    await screen.findByRole("link", { name: "posture corrector" });
    await user.click(screen.getByRole("button", { name: "Run posture corrector now" }));
    expect(await screen.findByText("Checking posture corrector now.")).toBeInTheDocument();
    expect(writes(calls)).toEqual([{ method: "POST", url: "/api/watch/1/run", body: undefined }]);
  });

  it("pauses an item from its switch", async () => {
    const { calls } = serveWatch({ items: [makeWatchItem()] });
    renderWatchlist();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("switch", { name: "Check posture corrector daily" }));
    await waitFor(() =>
      expect(writes(calls)).toEqual([
        { method: "PATCH", url: "/api/watch/1", body: { enabled: false } },
      ]),
    );
  });
});
