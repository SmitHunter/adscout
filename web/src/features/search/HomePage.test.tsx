import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider, useParams } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SearchEvent, Settings, Snapshot } from "@/api/client";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

import { HomePage } from "./HomePage";

/* ---- A fake EventSource the tests drive by hand. ---- */

class MockEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: MockEventSource[] = [];

  readonly url: string;
  readyState = MockEventSource.OPEN;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(kind: string, listener: (event: MessageEvent<string>) => void) {
    const set = this.listeners.get(kind) ?? new Set();
    set.add(listener);
    this.listeners.set(kind, set);
  }

  removeEventListener(kind: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.get(kind)?.delete(listener);
  }

  close() {
    this.readyState = MockEventSource.CLOSED;
  }

  emit(event: SearchEvent) {
    const message = new MessageEvent<string>(event.kind, { data: JSON.stringify(event) });
    act(() => {
      for (const listener of this.listeners.get(event.kind) ?? []) listener(message);
    });
  }

  static latest(): MockEventSource {
    const source = MockEventSource.instances.at(-1);
    if (source === undefined) throw new Error("No EventSource was opened");
    return source;
  }
}

/* ---- A fake API behind fetch. ---- */

interface Reply {
  status?: number;
  body: unknown;
}

const SETTINGS: Settings = {
  timezone: "Australia/Melbourne",
  max_results: 300,
  has_meta_token: false,
  version: "0.3.0",
  analysis: { available: false, backend: null, reason: null },
  analysis_backend: "auto",
  analysis_model: "claude-sonnet-5-5",
  auto_analyze: false,
  scroll_delay_min: 2,
  scroll_delay_max: 5,
  daily_check_time: "09:00",
  desktop_notifications: false,
  notifications_available: false,
  max_auto_analyses_per_day: 5,
  demo: false,
};

function snapshot(overrides: Partial<Snapshot>): Snapshot {
  return {
    id: 1,
    query: "posture corrector",
    kind: "keyword",
    page_id: null,
    country: "US",
    active_status: "active",
    media_type: "all",
    status: "ok",
    started_at: "2026-10-16T14:30:00Z",
    finished_at: "2026-10-16T14:32:00Z",
    ad_count: 184,
    advertiser_count: 41,
    error_message: null,
    ...overrides,
  };
}

function mockApi(routes: Record<string, Reply | (() => Reply)>) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
    calls.push({ method, url, body });
    const route = routes[`${method} ${url}`];
    if (route === undefined) {
      return new Response(JSON.stringify({ detail: `No mock for ${method} ${url}` }), {
        status: 500,
      });
    }
    const reply = typeof route === "function" ? route() : route;
    return new Response(reply.body === null ? "null" : JSON.stringify(reply.body), {
      status: reply.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function baseRoutes(
  overrides: Record<string, Reply | (() => Reply)> = {},
): Record<string, Reply | (() => Reply)> {
  return {
    "GET /api/settings": { body: SETTINGS },
    "GET /api/snapshots": { body: [snapshot({})] },
    "GET /api/searches/active": { body: null },
    ...overrides,
  };
}

function ReportStub() {
  const { id } = useParams();
  return <p>Report page {id}</p>;
}

function renderHome() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(
    [
      { path: "/", element: <HomePage /> },
      { path: "/report/:id", element: <ReportStub /> },
    ],
    { initialEntries: ["/"] },
  );
  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <RouterProvider router={router} />
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

function progress(overrides: Partial<SearchEvent>): SearchEvent {
  return {
    snapshot_id: 7,
    kind: "progress",
    ads_found: 0,
    advertisers: 0,
    phase: "Opening the Ad Library",
    message: null,
    queued: 0,
    ...overrides,
  };
}

/** Matches an element whose whole text (across child elements) equals `text`. */
function fullText(text: string) {
  return (_: string, element: Element | null) =>
    element !== null &&
    element.textContent?.replace(/\s+/g, " ").trim() === text &&
    Array.from(element.children).every(
      (child) => child.textContent?.replace(/\s+/g, " ").trim() !== text,
    );
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HomePage", () => {
  it("submits query and shows live counts", async () => {
    const calls = mockApi(
      baseRoutes({ "POST /api/searches": { status: 201, body: { snapshot_id: 7 } } }),
    );
    const user = userEvent.setup();
    renderHome();

    const keyword = await screen.findByRole("textbox", { name: "Product keyword" });
    expect(keyword).toHaveFocus();
    await user.type(keyword, "posture corrector{Enter}");

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    expect(MockEventSource.latest().url).toBe("/api/searches/7/events");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      kind: "keyword",
      keyword: "posture corrector",
      country: "US",
      active_status: "active",
      media_type: "all",
      limit: 300,
    });

    MockEventSource.latest().emit(
      progress({ ads_found: 1284, advertisers: 312, phase: "Scrolling for more ads" }),
    );

    expect(screen.getByText(fullText("Found 1,284 ads from 312 advertisers"))).toBeInTheDocument();
    expect(screen.getByText("Scrolling for more ads")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
  });

  it("navigates to report on done", async () => {
    mockApi(baseRoutes({ "POST /api/searches": { status: 201, body: { snapshot_id: 7 } } }));
    const user = userEvent.setup();
    renderHome();

    await user.type(
      await screen.findByRole("textbox", { name: "Product keyword" }),
      "led mask{Enter}",
    );
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    MockEventSource.latest().emit(
      progress({ kind: "done", ads_found: 40, advertisers: 12, phase: "Done" }),
    );

    expect(await screen.findByText("Report page 7")).toBeInTheDocument();
  });

  it("blocked shows open browser", async () => {
    const calls = mockApi(
      baseRoutes({
        "POST /api/searches": { status: 201, body: { snapshot_id: 7 } },
        "POST /api/browser/open": { status: 204, body: null },
      }),
    );
    const user = userEvent.setup();
    renderHome();

    await user.type(
      await screen.findByRole("textbox", { name: "Product keyword" }),
      "posture corrector{Enter}",
    );
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    MockEventSource.latest().emit(
      progress({
        kind: "blocked",
        phase: "Done",
        message:
          "Login required. Meta asked for login. Run: uv run adscout browser — sign in once, then retry.",
      }),
    );

    expect(screen.getByText("Meta asked for login.")).toBeInTheDocument();
    expect(screen.getByText(/sign in once, then retry/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open browser" }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url === "/api/browser/open")).toBe(true),
    );
  });

  it("reload reattaches to active search", async () => {
    mockApi(
      baseRoutes({
        "GET /api/searches/active": {
          body: progress({
            snapshot_id: 9,
            ads_found: 56,
            advertisers: 20,
            phase: "Scrolling for more ads",
          }),
        },
        "GET /api/snapshots": {
          body: [snapshot({ id: 9, query: "dog bed", status: "running", finished_at: null })],
        },
      }),
    );
    renderHome();

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    expect(MockEventSource.latest().url).toBe("/api/searches/9/events");
    expect(
      await screen.findByText(fullText("Found 56 ads from 20 advertisers")),
    ).toBeInTheDocument();

    MockEventSource.latest().emit(
      progress({ snapshot_id: 9, ads_found: 80, advertisers: 25, phase: "Scrolling for more ads" }),
    );
    expect(screen.getByText(fullText("Found 80 ads from 25 advertisers"))).toBeInTheDocument();
  });

  it("busy shows toast", async () => {
    mockApi(
      baseRoutes({
        "POST /api/searches": {
          status: 409,
          body: { detail: "A search is already running. Wait for it to finish or cancel it." },
        },
      }),
    );
    const user = userEvent.setup();
    renderHome();

    await user.type(
      await screen.findByRole("textbox", { name: "Product keyword" }),
      "posture corrector{Enter}",
    );

    expect(
      await screen.findByText("A search is already running. Wait for it to finish or cancel it."),
    ).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it("busy elsewhere attaches to the running search", async () => {
    let activeCalls = 0;
    mockApi(
      baseRoutes({
        "POST /api/searches": {
          status: 409,
          body: { detail: "A search is already running. Wait for it to finish or cancel it." },
        },
        // Idle on first load; the CLI has started a search by the time we submit.
        "GET /api/searches/active": () => {
          activeCalls += 1;
          return {
            body:
              activeCalls === 1
                ? null
                : progress({ snapshot_id: 5, ads_found: 31, advertisers: 8 }),
          };
        },
      }),
    );
    const user = userEvent.setup();
    renderHome();

    await user.type(
      await screen.findByRole("textbox", { name: "Product keyword" }),
      "posture corrector{Enter}",
    );

    expect(
      await screen.findByText("A search is already running. Wait for it to finish or cancel it."),
    ).toBeInTheDocument();
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    expect(MockEventSource.latest().url).toBe("/api/searches/5/events");
    expect(
      await screen.findByText(fullText("Found 31 ads from 8 advertisers")),
    ).toBeInTheDocument();
  });

  it("disables the suggestion while a search starts", async () => {
    const calls = mockApi(
      baseRoutes({
        "GET /api/snapshots": { body: [] },
        "POST /api/searches": { status: 201, body: { snapshot_id: 3 } },
      }),
    );
    const user = userEvent.setup();
    renderHome();

    const suggestion = await screen.findByRole("button", { name: 'Try "posture corrector" in US' });
    await user.click(suggestion);
    await user.click(suggestion);

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("recent search time is focusable and still opens the report", async () => {
    mockApi(baseRoutes({ "GET /api/snapshots": { body: [snapshot({ id: 4 })] } }));
    const user = userEvent.setup();
    renderHome();

    const table = await screen.findByRole("table", { name: "Recent searches" });
    const time = table.querySelector("time");
    expect(time).not.toBeNull();
    expect(time).toHaveAttribute("tabindex", "0");
    await user.click(time!);
    expect(await screen.findByText("Report page 4")).toBeInTheDocument();
  });

  it("recent search time opens the report on Enter", async () => {
    mockApi(baseRoutes({ "GET /api/snapshots": { body: [snapshot({ id: 4 })] } }));
    const user = userEvent.setup();
    renderHome();

    const table = await screen.findByRole("table", { name: "Recent searches" });
    const time = table.querySelector("time");
    expect(time).not.toBeNull();
    time!.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Report page 4")).toBeInTheDocument();
  });

  it("empty state suggestion submits", async () => {
    const calls = mockApi(
      baseRoutes({
        "GET /api/snapshots": { body: [] },
        "POST /api/searches": { status: 201, body: { snapshot_id: 3 } },
      }),
    );
    const user = userEvent.setup();
    renderHome();

    expect(
      await screen.findByText("Search a product to see if it's a proven winner."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: 'Try "posture corrector" in US' }));

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
      keyword: "posture corrector",
      country: "US",
    });
    expect(screen.getByRole("textbox", { name: "Product keyword" })).toHaveValue(
      "posture corrector",
    );
  });

  it("lists recent searches that link to their reports", async () => {
    mockApi(
      baseRoutes({
        "GET /api/snapshots": {
          body: [
            snapshot({ id: 4, query: "led mask", country: "AU", ad_count: 1203 }),
            snapshot({ id: 2, query: "dog bed", status: "blocked", ad_count: 0 }),
            snapshot({ id: 5, query: "neck fan", status: "queued", ad_count: 0 }),
          ],
        },
      }),
    );
    renderHome();

    const table = await screen.findByRole("table", { name: "Recent searches" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(within(table).getByRole("link", { name: "led mask" })).toHaveAttribute(
      "href",
      "/report/4",
    );
    expect(within(rows[1]!).getByText("1,203")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Blocked")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("Queued")).toBeInTheDocument();
  });

  it("shows EU as unavailable without a Meta token", async () => {
    mockApi(baseRoutes());
    const user = userEvent.setup();
    renderHome();

    const eu = await screen.findByRole("radio", { name: "EU" });
    expect(eu).toHaveAttribute("aria-disabled", "true");
    await user.click(eu);
    expect(eu).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("radio", { name: "US" })).toHaveAttribute("aria-checked", "true");
  });

  it("cancel asks the server to stop and shows the cancelled state", async () => {
    const calls = mockApi(
      baseRoutes({
        "POST /api/searches": { status: 201, body: { snapshot_id: 7 } },
        "POST /api/searches/7/cancel": { body: { cancelled: true } },
      }),
    );
    const user = userEvent.setup();
    renderHome();

    await user.type(
      await screen.findByRole("textbox", { name: "Product keyword" }),
      "posture corrector{Enter}",
    );
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/searches/7/cancel")).toBe(true));

    MockEventSource.latest().emit(
      progress({ kind: "cancelled", ads_found: 22, advertisers: 9, phase: "Done" }),
    );
    expect(screen.getByText("Search cancelled. Partial results were saved.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open report" })).toHaveAttribute("href", "/report/7");
  });

  it("one live region announces the phases and then the outcome", async () => {
    mockApi(baseRoutes({ "POST /api/searches": { status: 201, body: { snapshot_id: 7 } } }));
    const user = userEvent.setup();
    renderHome();

    await user.type(
      await screen.findByRole("textbox", { name: "Product keyword" }),
      "posture corrector{Enter}",
    );
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    MockEventSource.latest().emit(
      progress({ ads_found: 3, advertisers: 2, phase: "Scrolling for more ads" }),
    );
    const live = screen.getByRole("status", { name: "Search status" });
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveTextContent("Scrolling for more ads.");

    MockEventSource.latest().emit(
      progress({ kind: "error", ads_found: 3, advertisers: 2, phase: "Done", message: "Boom." }),
    );
    // The same element, still mounted, now carries the outcome.
    expect(screen.getByRole("status", { name: "Search status" })).toBe(live);
    expect(live).toHaveTextContent("Search failed.");
  });
});
