import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CompareColumn, Snapshot } from "@/api/client";
import { renderRoutes, stubResizeObserver } from "@/features/watch/testing";

import { ComparePage } from "./ComparePage";
import { makeColumn, makeFactors, makeSnapshot } from "./fixtures";

const SNAPSHOTS: Snapshot[] = [
  makeSnapshot({ id: 1, query: "posture corrector", country: "US" }),
  makeSnapshot({ id: 2, query: "neck massager", country: "AU" }),
  makeSnapshot({ id: 3, query: "led face mask", country: "GB" }),
  makeSnapshot({ id: 4, query: "pet hair remover", country: "US" }),
  makeSnapshot({ id: 5, query: "ice roller", country: "CA" }),
];

function columnFor(id: number): CompareColumn {
  const snapshot = SNAPSHOTS.find((s) => s.id === id) ?? makeSnapshot({ id });
  if (id === 2) {
    // Longer-running but thinner: wins longevity, loses continuity and volume.
    return makeColumn({
      snapshot,
      score: 64,
      leader_factors: makeFactors({ longevity: 300, continuity: 3, volume: 6 }),
      analysis: { verdict: "A steady seller with one dominant store." },
    });
  }
  return makeColumn({
    snapshot,
    leader_factors: makeFactors({ longevity: 120, continuity: 6, volume: 12 }),
  });
}

function serve() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body)));
      if (url === "/api/snapshots") return json(SNAPSHOTS);
      if (url.startsWith("/api/compare?ids=")) {
        const ids = decodeURIComponent(url.split("=")[1] ?? "")
          .split(",")
          .map(Number);
        return json(ids.map(columnFor));
      }
      return json(null);
    }),
  );
  return calls;
}

function renderCompare(path: string) {
  return renderRoutes([{ path: "/compare", element: <ComparePage /> }], path);
}

beforeEach(() => stubResizeObserver());
afterEach(() => vi.unstubAllGlobals());

describe("ComparePage", () => {
  it("compare aligns factor rows and highlights best", async () => {
    const calls = serve();
    renderCompare("/compare?ids=1,2");

    const longevity = await screen.findByTestId("compare-row-longevity");
    expect(calls).toContain("/api/compare?ids=1,2");

    // Every factor is one row with one cell per column, in column order.
    for (const key of ["longevity", "continuity", "volume", "angles"]) {
      const cells = within(screen.getByTestId(`compare-row-${key}`)).getAllByTestId("compare-cell");
      expect(cells.map((c) => c.dataset.snapshot)).toEqual(["1", "2"]);
    }

    const best = (key: string) =>
      within(screen.getByTestId(`compare-row-${key}`))
        .getAllByTestId("compare-cell")
        .map((c) => c.dataset.best);
    expect(best("longevity")).toEqual(["false", "true"]);
    expect(best("continuity")).toEqual(["true", "false"]);
    expect(best("volume")).toEqual(["true", "false"]);
    // Nobody has an angle count yet: nothing to highlight.
    expect(best("angles")).toEqual(["false", "false"]);

    const winner = within(longevity).getAllByTestId("compare-cell")[1] as HTMLElement;
    expect(winner).toHaveClass("outline-signal");
    expect(within(winner).getByText("Best")).toBeInTheDocument();

    // Column extras: headline scores, verdicts where analysed.
    expect(screen.getAllByTestId("compare-score").map((n) => n.textContent)).toEqual(["91", "64"]);
    expect(screen.getByText("A steady seller with one dominant store.")).toBeInTheDocument();
    expect(screen.getByText("Not analysed yet.")).toBeInTheDocument();
    // Headers carry the check's time, so two checks of one search on one day differ.
    expect(screen.getAllByText("17 Oct 2026 15:01")).toHaveLength(2);
  });

  it("compare picker limits to four", async () => {
    serve();
    const user = userEvent.setup();
    renderCompare("/compare?ids=1,2,3");

    await screen.findByTestId("compare-row-longevity");
    await user.click(screen.getByRole("button", { name: /Choose searches/ }));

    const box = (name: RegExp) => screen.getByRole("checkbox", { name });
    await waitFor(() => expect(box(/pet hair remover/)).toBeEnabled());
    expect(box(/posture corrector/)).toBeChecked();

    await user.click(box(/pet hair remover/));
    expect(box(/pet hair remover/)).toBeChecked();
    // Four picked: the rest can't be added until one is removed.
    expect(box(/ice roller/)).toBeDisabled();
    expect(screen.getByText("Remove one to add another.")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getAllByTestId("compare-score").map((n) => n.textContent)).toHaveLength(4),
    );

    await user.click(box(/led face mask/));
    expect(box(/ice roller/)).toBeEnabled();
  });

  it("asks for two searches before comparing", async () => {
    const calls = serve();
    renderCompare("/compare?ids=1");

    expect(
      await screen.findByText("Pick two to four searches to compare them side by side."),
    ).toBeInTheDocument();
    expect(calls.some((c) => c.startsWith("/api/compare"))).toBe(false);
  });

  it("best goes to the most points, not the biggest raw value", async () => {
    serve();
    const pointsFor = (value: number, points: number) =>
      makeFactors().map((f) =>
        f.key === "longevity"
          ? { ...f, value, display: `${value} days`, points }
          : f.key === "volume"
            ? // Equal points: the raw value breaks the tie.
              { ...f, value: value === 261 ? 30 : 12, points: 25 }
            : f,
      );
    const columns = [
      makeColumn({ snapshot: SNAPSHOTS[0]!, leader_factors: pointsFor(261, 23.8) }),
      makeColumn({ snapshot: SNAPSHOTS[1]!, leader_factors: pointsFor(130, 27.5) }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.startsWith("/api/compare")
          ? columns
          : url === "/api/snapshots"
            ? SNAPSHOTS
            : null;
        return Promise.resolve(new Response(JSON.stringify(body)));
      }),
    );
    renderCompare("/compare?ids=1,2");

    const best = async (key: string) =>
      within(await screen.findByTestId(`compare-row-${key}`))
        .getAllByTestId("compare-cell")
        .map((c) => c.dataset.best);
    expect(await best("longevity")).toEqual(["false", "true"]);
    expect(await best("volume")).toEqual(["true", "false"]);
    // Same points and same value everywhere: nothing stands out.
    expect(await best("continuity")).toEqual(["false", "false"]);
  });
});
