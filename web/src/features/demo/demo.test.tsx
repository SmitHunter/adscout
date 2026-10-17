import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Shell } from "@/app/Shell";
import { TooltipProvider } from "@/components/ui/tooltip";
import { HomePage } from "@/features/search/HomePage";
import { makeSettings } from "@/features/report/fixtures";

import { DEMO_COPY } from "./demo";

function serve(demo: boolean) {
  const calls: { method: string; url: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ method, url });
      let body: unknown = null;
      if (url === "/api/settings") body = makeSettings({ demo });
      else if (url === "/api/snapshots" || url.startsWith("/api/alerts")) body = [];
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }),
  );
  return calls;
}

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [{ path: "/", element: <Shell />, children: [{ index: true, element: <HomePage /> }] }],
    { initialEntries: ["/"] },
  );
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("demo mode", () => {
  it("demo banner and disabled search", async () => {
    const calls = serve(true);
    const user = userEvent.setup();
    renderApp();

    expect(await screen.findByText(DEMO_COPY, { selector: "p" })).toBeInTheDocument();
    const search = screen.getByRole("button", { name: "Search" });
    await waitFor(() => expect(search).toBeDisabled());

    const lock = screen.getByTestId("demo-lock");
    expect(lock).toHaveAccessibleName(DEMO_COPY);
    act(() => lock.focus());
    expect(await screen.findByRole("tooltip")).toHaveTextContent(DEMO_COPY);

    await user.type(screen.getByRole("textbox", { name: "Product keyword" }), "brace{Enter}");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("shows no banner outside the demo", async () => {
    serve(false);
    renderApp();
    const search = await screen.findByRole("button", { name: "Search" });
    await waitFor(() => expect(screen.getByRole("textbox")).toBeInTheDocument());
    expect(screen.queryByText(DEMO_COPY)).toBeNull();
    expect(screen.queryByTestId("demo-lock")).toBeNull();
    expect(search).not.toBeDisabled();
  });
});
