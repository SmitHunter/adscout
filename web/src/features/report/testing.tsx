import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

import type { AnalysisJob, Report, Settings } from "@/api/client";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

import { ReportPage } from "./ReportPage";

export interface Call {
  method: string;
  url: string;
  body: unknown;
}

/**
 * A fake API for the report page. `report()` is read on every GET, so a test
 * can swap the report a write produces; every request is recorded in `calls`.
 */
export interface ServeExtras {
  /** GET /api/settings; null when left out. */
  settings?: Settings;
  /** GET …/analysis, read on every poll. */
  job?: () => AnalysisJob | null;
  /** POST …/analysis: the status and body to answer with. */
  start?: () => { status: number; body: unknown };
}

export function serveReport(
  report: () => Report,
  /** Fail the n-th override write (1-based) with this API detail. */
  fail?: { at: number; detail: string },
  extras: ServeExtras = {},
) {
  const calls: Call[] = [];
  let writes = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
      calls.push({ method, url, body });
      if (method === "GET" && url.endsWith("/report")) {
        return Promise.resolve(new Response(JSON.stringify(report()), { status: 200 }));
      }
      if (url === "/api/settings" && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify(extras.settings ?? null)));
      }
      if (url.endsWith("/analysis")) {
        if (method === "POST" && extras.start) {
          const { status, body } = extras.start();
          return Promise.resolve(new Response(JSON.stringify(body), { status }));
        }
        return Promise.resolve(new Response(JSON.stringify(extras.job?.() ?? null)));
      }
      if (url === "/api/overrides") {
        writes += 1;
        if (fail !== undefined && writes === fail.at) {
          return Promise.resolve(
            new Response(JSON.stringify({ detail: fail.detail }), { status: 500 }),
          );
        }
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      return Promise.resolve(new Response(JSON.stringify(null), { status: 200 }));
    }),
  );
  return calls;
}

export function renderReport(path = "/report/1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: "/report/:id", element: <ReportPage /> }], {
    initialEntries: [path],
  });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RouterProvider router={router} />
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

export function reportGets(calls: Call[]): number {
  return calls.filter((c) => c.method === "GET" && c.url.endsWith("/report")).length;
}
