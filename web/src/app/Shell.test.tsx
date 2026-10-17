import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Snapshot } from "@/api/client";

import { Shell } from "./Shell";

function snapshot(
  id: number,
  query: string,
  status: string,
  overrides: Partial<Snapshot> = {},
): Snapshot {
  return {
    id,
    query,
    kind: "keyword",
    page_id: null,
    country: "US",
    active_status: "active",
    media_type: "all",
    status,
    started_at: "2026-10-16T14:30:00Z",
    finished_at: null,
    ad_count: 0,
    advertiser_count: 0,
    error_message: null,
    ...overrides,
  };
}

function renderShell() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [{ path: "/", element: <Shell />, children: [{ index: true, element: <p>home</p> }] }],
    { initialEntries: ["/"] },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

function serve(snapshots: Snapshot[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.endsWith("/api/snapshots") ? snapshots : null;
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Shell rail", () => {
  it("labels queued and running searches", async () => {
    serve([snapshot(2, "neck fan", "queued"), snapshot(1, "led mask", "running")]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter(
      [{ path: "/", element: <Shell />, children: [{ index: true, element: <p>home</p> }] }],
      { initialEntries: ["/"] },
    );
    render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Queued")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("lists only the latest run of each search", async () => {
    serve([
      snapshot(9, "posture brace", "ok", { ad_count: 132 }),
      snapshot(8, "posture brace", "ok", { ad_count: 126 }),
      snapshot(7, "Posture Brace", "ok", { ad_count: 123 }),
      snapshot(6, "posture brace", "ok", { country: "AU", ad_count: 40 }),
      snapshot(5, "Alignly", "ok", { kind: "advertiser", page_id: "101", country: "ALL" }),
      snapshot(4, "Alignly Store", "ok", { kind: "advertiser", page_id: "101", country: "ALL" }),
      snapshot(3, "led face mask", "ok"),
    ]);
    renderShell();

    const links = await screen.findAllByRole("link", { name: /posture brace|alignly|led face/i });
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/report/9",
      "/report/6",
      "/report/5",
      "/report/3",
    ]);
  });

  it("contains absolutely positioned content in the scrolling main", async () => {
    // Without a positioned <main>, sr-only text deep in a long report is placed against the
    // page instead, so the window itself scrolls and slides the shell off screen.
    serve([]);
    renderShell();

    const main = await screen.findByRole("main");
    expect(main).toHaveClass("relative");
  });
});
