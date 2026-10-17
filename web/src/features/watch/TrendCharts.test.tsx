import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeAlert, makePoints, makeWatchItem } from "./fixtures";
import { renderRoutes, serveWatch, stubResizeObserver } from "./testing";
import { TrendCharts } from "./TrendCharts";
import { WatchItemPage } from "./WatchItemPage";

beforeEach(stubResizeObserver);
afterEach(() => vi.unstubAllGlobals());

describe("TrendCharts", () => {
  it("trend charts mark capped points", async () => {
    const points = makePoints(6);
    const { container } = render(<TrendCharts points={points} />);
    const user = userEvent.setup();

    // Four small multiples over one shared x-axis.
    for (const name of ["Active ads", "New creatives per check", "Advertisers", "Winner Score"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(container.querySelectorAll("[data-axis='x']")).toHaveLength(1);

    // The third check hit the search limit: a hollow dot, titled.
    const capped = container.querySelectorAll("[data-capped='true']");
    expect(capped).toHaveLength(1);
    expect(capped[0]?.querySelector("title")?.textContent).toBe("At the search limit");

    // The readout starts on the latest check, then follows the keyboard.
    const readout = screen.getByTestId("trend-readout");
    expect(within(readout).getByText("196")).toBeInTheDocument();
    expect(within(readout).queryByText("At the search limit")).toBeNull();

    screen.getByRole("group", { name: /Trend charts/ }).focus();
    await user.keyboard("{Home}{ArrowRight}{ArrowRight}");
    expect(within(readout).getByText("136")).toBeInTheDocument();
    expect(within(readout).getByText("At the search limit")).toBeInTheDocument();
    expect(within(readout).getByText("6 Oct")).toBeInTheDocument();
  });

  it("watch item page lists its alerts and links each check's report", async () => {
    serveWatch({
      items: [makeWatchItem()],
      points: makePoints(3),
      alerts: [
        makeAlert(),
        makeAlert({ id: 2, watch_item_id: 9, message: "neck fan: active ads down 40% (50 → 30)" }),
      ],
    });
    renderRoutes([{ path: "/watch/:id", element: <WatchItemPage /> }], "/watch/1");

    expect(await screen.findByRole("heading", { name: "posture corrector" })).toBeInTheDocument();
    expect(
      await screen.findByText("posture corrector: active ads up 38% (132 → 182)"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/neck fan/)).toBeNull();
    const links = screen.getAllByRole("link", { name: /Open report/ });
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/report/3",
      "/report/2",
      "/report/1",
    ]);
  });
});
