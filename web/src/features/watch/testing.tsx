import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";
import { vi } from "vitest";

import type {
  Report,
  SearchQuery,
  Settings,
  TrendPoint,
  WatchAlert,
  WatchItem,
} from "@/api/client";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { makeSettings } from "@/features/report/fixtures";

import { makeWatchItem } from "./fixtures";

export interface Call {
  method: string;
  url: string;
  body: unknown;
}

export interface WatchWorld {
  items: WatchItem[];
  alerts: WatchAlert[];
  points: TrendPoint[];
  settings: Settings;
  report?: Report;
}

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

/** A fake AdScout API over an in-memory watchlist; every request lands in `calls`. */
export function serveWatch(initial: Partial<WatchWorld> = {}) {
  const world: WatchWorld = {
    items: [],
    alerts: [],
    points: [],
    settings: makeSettings(),
    ...initial,
  };
  const calls: Call[] = [];
  let nextId = 100;

  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
      calls.push({ method, url, body });
      const path = url.split("?")[0] ?? url;
      const itemMatch = /^\/api\/watch\/(\d+)(\/run|\/trend)?$/.exec(path);

      if (path === "/api/settings") {
        if (method === "PUT") world.settings = { ...world.settings, ...(body as object) };
        return json(world.settings);
      }
      if (path === "/api/watch" && method === "GET") return json(world.items);
      if (path === "/api/watch" && method === "POST") {
        const q = body as SearchQuery;
        const exists = world.items.some(
          (i) =>
            i.kind === q.kind &&
            i.country === q.country &&
            (q.kind === "advertiser" ? i.page_id === q.page_id : i.query === q.keyword),
        );
        if (exists) return json({ detail: "Already watching this." }, 409);
        const item = makeWatchItem({
          id: nextId++,
          kind: q.kind,
          query: q.keyword,
          page_id: q.page_id ?? null,
          country: q.country,
          active_status: q.active_status,
          media_type: q.media_type,
          last_run_at: null,
          last_status: null,
          latest_point: null,
          sparkline: [],
        });
        world.items = [...world.items, item];
        return json(item, 201);
      }
      if (itemMatch) {
        const id = Number(itemMatch[1]);
        const item = world.items.find((i) => i.id === id);
        if (item === undefined) return json({ detail: `Watch item ${id} not found.` }, 404);
        if (itemMatch[2] === "/run" && method === "POST") {
          return json({ message: `Checking ${item.query} now.` }, 202);
        }
        if (itemMatch[2] === "/trend") return json({ item, points: world.points });
        if (method === "DELETE") {
          world.items = world.items.filter((i) => i.id !== id);
          return json(null, 204);
        }
        if (method === "PATCH") {
          const next = { ...item, ...(body as Partial<WatchItem>) };
          world.items = world.items.map((i) => (i.id === id ? next : i));
          return json(next);
        }
      }
      if (path === "/api/alerts") {
        const unread = url.includes("unread=true");
        return json(world.alerts.filter((a) => !unread || !a.read));
      }
      if (path === "/api/alerts/read" && method === "POST") {
        const { ids, all } = body as { ids?: number[]; all?: boolean };
        let marked = 0;
        world.alerts = world.alerts.map((a) => {
          if (!a.read && (all === true || ids?.includes(a.id))) {
            marked += 1;
            return { ...a, read: true };
          }
          return a;
        });
        return json({ marked });
      }
      if (path.endsWith("/report") && world.report) return json(world.report);
      if (path === "/api/snapshots") return json([]);
      return json(null);
    }),
  );
  return { world, calls };
}

export function writes(calls: Call[]): Call[] {
  return calls.filter((c) => c.method !== "GET");
}

export function renderRoutes(routes: RouteObject[], path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RouterProvider router={router} />
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

/** jsdom has no ResizeObserver; Radix and the charts measure with it. */
export function stubResizeObserver() {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
}
