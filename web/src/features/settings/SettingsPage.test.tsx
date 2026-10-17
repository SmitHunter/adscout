import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Settings } from "@/api/client";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { makeSettings } from "@/features/report/fixtures";

import { SettingsPage } from "./SettingsPage";

beforeEach(() => {
  // Radix sizes the switch's hidden form input; jsdom has no ResizeObserver.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => vi.unstubAllGlobals());

const SCROLL_ERROR = "Scroll delays must satisfy 0.5 ≤ min ≤ max ≤ 30.";

function serveSettings(initial: Settings, reject?: string) {
  let current = initial;
  const puts: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/settings" && init?.method === "PUT") {
        const patch = JSON.parse(String(init.body)) as Partial<Settings>;
        puts.push(patch);
        const next = { ...current, ...patch };
        if (reject !== undefined) {
          return Promise.resolve(new Response(JSON.stringify({ detail: reject }), { status: 422 }));
        }
        if (next.scroll_delay_min > next.scroll_delay_max) {
          return Promise.resolve(
            new Response(JSON.stringify({ detail: SCROLL_ERROR }), { status: 422 }),
          );
        }
        current = next;
        return Promise.resolve(new Response(JSON.stringify(current)));
      }
      if (url === "/api/settings") return Promise.resolve(new Response(JSON.stringify(current)));
      return Promise.resolve(new Response(JSON.stringify(null)));
    }),
  );
  return puts;
}

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: "/settings", element: <SettingsPage /> }], {
    initialEntries: ["/settings"],
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

describe("SettingsPage", () => {
  it("settings saves and shows inline validation", async () => {
    const puts = serveSettings(makeSettings());
    renderSettings();
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: "Auto" })).toHaveAttribute("data-state", "on");
    expect(screen.getByText("Ready. Auto is using Claude Code.")).toBeInTheDocument();
    expect(screen.getByText("Australia/Melbourne")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: "Anthropic API" }));
    await user.click(screen.getByRole("switch", { name: "Analyze angles after each search" }));
    const limit = screen.getByRole("spinbutton", { name: "Search limit" });
    await user.clear(limit);
    await user.type(limit, "500");
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByText("Settings saved")).toBeInTheDocument();
    expect(puts).toEqual([{ analysis_backend: "api", auto_analyze: true, max_results: 500 }]);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled(),
    );

    const min = screen.getByRole("spinbutton", { name: "Shortest delay" });
    await user.clear(min);
    await user.type(min, "9");
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByText(SCROLL_ERROR)).toBeInTheDocument();
    expect(min).toHaveAttribute("aria-invalid", "true");
    expect(puts.at(-1)).toEqual({ scroll_delay_min: 9 });
  });

  it("a 422 that names no field shows under the form", async () => {
    const detail = "Something else is wrong with these settings.";
    serveSettings(makeSettings(), detail);
    renderSettings();
    const user = userEvent.setup();

    const limit = await screen.findByRole("spinbutton", { name: "Search limit" });
    await user.clear(limit);
    await user.type(limit, "500");
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(detail);
    expect(limit).not.toHaveAttribute("aria-invalid", "true");
  });

  it("unavailable analysis shows the setup copy", async () => {
    serveSettings(
      makeSettings({
        analysis: {
          available: false,
          backend: null,
          reason:
            "Angle analysis needs Claude. Install Claude Code or set ANTHROPIC_API_KEY in .env, then restart AdScout.",
        },
      }),
    );
    renderSettings();

    expect(
      await screen.findByText(
        "Angle analysis needs Claude. Install Claude Code or set ANTHROPIC_API_KEY in .env, then restart AdScout.",
      ),
    ).toBeInTheDocument();
  });

  it("settings daily time validation", async () => {
    const puts = serveSettings(makeSettings());
    renderSettings();
    const user = userEvent.setup();

    const time = await screen.findByLabelText("Daily check time");
    expect(time).toHaveValue("09:00");
    await user.clear(time);
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Enter a time, like 09:00.")).toBeInTheDocument();
    expect(time).toHaveAttribute("aria-invalid", "true");
    expect(puts).toEqual([]);

    await user.type(time, "07:30");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Settings saved")).toBeInTheDocument();
    expect(puts).toEqual([{ daily_check_time: "07:30" }]);
    expect(screen.queryByText("Enter a time, like 09:00.")).toBeNull();
  });

  it("desktop notifications need the extra installed", async () => {
    serveSettings(makeSettings({ notifications_available: false }));
    renderSettings();
    const user = userEvent.setup();

    const toggle = await screen.findByRole("switch", { name: "Desktop notifications" });
    expect(toggle).toBeDisabled();
    await user.hover(toggle.parentElement as HTMLElement);
    expect(
      (await screen.findAllByText("Install with: uv sync --extra notify")).length,
    ).toBeGreaterThan(0);
  });
});
