import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Report } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";

import { makeAnalysis, makeFactor, makeReport } from "./fixtures";
import { ReportPage } from "./ReportPage";

function mockReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: reduce && query.includes("prefers-reduced-motion"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

function serve(report: Report | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/report")) {
        return Promise.resolve(
          report
            ? new Response(JSON.stringify(report), { status: 200 })
            : new Response(JSON.stringify({ detail: "Snapshot not found." }), { status: 404 }),
        );
      }
      return Promise.resolve(new Response(JSON.stringify(null), { status: 200 }));
    }),
  );
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: "/report/:id", element: <ReportPage /> }], {
    initialEntries: [path],
  });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => mockReducedMotion(false));
afterEach(() => vi.unstubAllGlobals());

describe("ReportPage", () => {
  it("shows the winner score, its leader and the market line", async () => {
    serve(makeReport());
    renderAt("/report/1");

    expect(await screen.findByRole("heading", { name: "posture corrector" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("winner-score").textContent).toBe("91"));
    expect(screen.getByTestId("winner-score")).toHaveClass("text-pass");
    expect(screen.getByTestId("leader-counts").textContent).toBe("30 ads, 27 creatives");
    expect(screen.getByTestId("market-demand").textContent).toBe("98");
    expect(screen.getByText("Crowded")).toBeInTheDocument();
    expect(screen.getByText("Provisional: angle analysis not run yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search again" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Export" })).toBeEnabled();
    expect(screen.getAllByTestId("ruler-track")).toHaveLength(3);
  });

  it("provisional badge hidden after analysis", async () => {
    const analysed = makeReport({ analysis: makeAnalysis() });
    analysed.score.provisional = false;
    analysed.score.factors = analysed.score.factors.map((f) =>
      f.key === "angles"
        ? makeFactor({
            key: "angles",
            label: "Angle diversity",
            value: 4,
            display: "4 angles",
            threshold: { fail: 2, pass: 3, full: 5 },
            status: "pass",
            score: 0.85,
            points: 21.25,
            max_points: 25,
            detail: "Desk-job back pain, Look taller and confident, Gift for parents",
          })
        : f,
    );
    analysed.score.leader = null;
    serve(analysed);
    renderAt("/report/1");

    expect(await screen.findByTestId("winner-score")).toBeInTheDocument();
    expect(screen.queryByText("Provisional: angle analysis not run yet")).toBeNull();
    expect(screen.getAllByTestId("ruler-track").map((t) => t.getAttribute("data-hatched"))).toEqual(
      ["false", "false", "false"],
    );
  });

  it("zero-ad snapshot shows notice and no charts", async () => {
    const empty = makeReport({ notice: "No ads found for this search." });
    empty.snapshot.ad_count = 0;
    serve(empty);
    renderAt("/report/1");

    expect(await screen.findByText("No ads found for this search.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search again" })).toBeInTheDocument();
    expect(screen.queryByTestId("winner-score")).toBeNull();
    expect(screen.queryByTestId("ruler-track")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("No launch dates yet.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Export" })).toBeNull();
  });

  it("a queued snapshot says it is waiting for another search", async () => {
    const queued = makeReport({
      notice: "This search is waiting for another search to finish.",
    });
    queued.snapshot.ad_count = 0;
    queued.snapshot.status = "queued";
    serve(queued);
    renderAt("/report/1");

    expect(
      await screen.findByText("This search is waiting for another search to finish."),
    ).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  it("search again re-runs an advertiser snapshot as an advertiser search", async () => {
    const posts: unknown[] = [];
    const report = makeReport();
    report.snapshot = {
      ...report.snapshot,
      kind: "advertiser",
      page_id: "1234567",
      query: "Alpha Store",
      country: "ALL",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/report")) {
          return Promise.resolve(new Response(JSON.stringify(report), { status: 200 }));
        }
        if (init?.method === "POST") {
          posts.push(JSON.parse(String(init.body)));
          return Promise.resolve(new Response(JSON.stringify({ snapshot_id: 9 }), { status: 201 }));
        }
        return Promise.resolve(new Response(JSON.stringify(null), { status: 200 }));
      }),
    );
    renderAt("/report/1");

    (await screen.findByRole("button", { name: "Search again" })).click();
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({
      kind: "advertiser",
      page_id: "1234567",
      keyword: "Alpha Store",
      country: "ALL",
    });
  });

  it("zero-ad report without a notice falls back to the CLI wording", async () => {
    const empty = makeReport({ notice: null });
    empty.snapshot.ad_count = 0;
    serve(empty);
    renderAt("/report/1");

    expect(
      await screen.findByText('No ads found for "posture corrector" in US. Try a broader keyword.'),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search again" })).toBeInTheDocument();
    expect(screen.queryByTestId("winner-score")).toBeNull();
  });

  it("blocked zero-ad report shows why it stopped, without CLI commands", async () => {
    const blocked = makeReport({ notice: null });
    blocked.snapshot.ad_count = 0;
    blocked.snapshot.status = "blocked";
    blocked.snapshot.error_message =
      "Login required. Run: uv run adscout browser, sign in, then retry.";
    serve(blocked);
    renderAt("/report/1");

    expect(
      await screen.findByText("Login required. Open the browser, sign in, then retry."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/uv run/)).toBeNull();
    expect(screen.getByRole("button", { name: "Search again" })).toBeInTheDocument();
  });

  it("a capped report shows a neutral limit notice", async () => {
    const capped = makeReport({
      notice: "Stopped at the 300 ad limit. Raise the limit to see more.",
    });
    capped.snapshot.status = "capped";
    serve(capped);
    renderAt("/report/1");

    const text = await screen.findByText(
      "Stopped at the 300 ad limit. Raise the limit to see more.",
    );
    const notice = text.closest("[role=status]");
    expect(notice?.className).toContain("border-l-muted");
    expect(notice?.className).not.toContain("border-l-warn");
  });

  it("a cancelled report keeps the partial-data warning style", async () => {
    const cancelled = makeReport({
      notice: "Partial data: this snapshot stopped early (cancelled).",
    });
    cancelled.snapshot.status = "cancelled";
    serve(cancelled);
    renderAt("/report/1");

    const text = await screen.findByText("Partial data: this snapshot stopped early (cancelled).");
    expect(text.closest("[role=status]")?.className).toContain("border-l-warn");
  });

  it("reduced motion skips count-up", async () => {
    mockReducedMotion(true);
    serve(makeReport());
    renderAt("/report/1");

    const score = await screen.findByTestId("winner-score");
    expect(score.textContent).toBe("91");
  });

  it("counts the score up when motion is allowed", async () => {
    serve(makeReport());
    renderAt("/report/1");

    const score = await screen.findByTestId("winner-score");
    expect(score.textContent).toBe("0");
    await waitFor(() => expect(score.textContent).toBe("91"));
  });

  it("treats a non-numeric id as not found", () => {
    serve(makeReport());
    renderAt("/report/abc");
    expect(screen.getByRole("heading", { name: "This report doesn't exist." })).toBeInTheDocument();
  });

  it("shows not found when the API has no such snapshot", async () => {
    serve(null);
    renderAt("/report/99");
    expect(
      await screen.findByRole("heading", { name: "This report doesn't exist." }),
    ).toBeInTheDocument();
  });
});
