import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Shell } from "@/app/Shell";

import { makeAlert } from "./fixtures";
import { renderRoutes, serveWatch, stubResizeObserver, writes } from "./testing";

beforeEach(stubResizeObserver);
afterEach(() => vi.unstubAllGlobals());

describe("AlertsMenu", () => {
  it("alerts badge and mark all read", async () => {
    const { calls } = serveWatch({
      alerts: [
        makeAlert({ id: 3 }),
        makeAlert({
          id: 2,
          kind: "blocked",
          change_pct: null,
          snapshot_id: null,
          message: "Meta blocked the daily check. Open the app to sign in.",
          created_at: "2026-10-15T22:00:00Z",
        }),
        makeAlert({ id: 1, read: true, message: "neck fan: active ads down 40% (50 → 30)" }),
      ],
    });
    renderRoutes(
      [
        {
          path: "/",
          element: <Shell />,
          children: [{ index: true, element: <p>home</p> }],
        },
      ],
      "/",
    );
    const user = userEvent.setup();

    const watchlist = await screen.findByRole("link", { name: "Watchlist, 2 unread alerts" });
    expect(within(watchlist).getByText("2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Alerts, 2 unread" }));
    const menu = await screen.findByRole("dialog", { name: "Alerts" });
    expect(
      within(menu).getByText("posture corrector: active ads up 38% (132 → 182)"),
    ).toBeInTheDocument();
    expect(
      within(menu).getByText("Meta blocked the daily check. Open the app to sign in."),
    ).toBeInTheDocument();
    expect(within(menu).getByText("neck fan: active ads down 40% (50 → 30)")).toBeInTheDocument();

    await user.click(within(menu).getByRole("button", { name: "Mark all read" }));
    await waitFor(() =>
      expect(screen.getByRole("link", { name: "Watchlist" })).toBeInTheDocument(),
    );
    expect(writes(calls)).toEqual([
      { method: "POST", url: "/api/alerts/read", body: { all: true } },
    ]);
    expect(screen.getByRole("button", { name: "Alerts" })).toBeInTheDocument();
  });
});
