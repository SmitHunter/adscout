import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./client";

describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("explains a network failure without naming a CLI command", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error: unknown = await api.get("/api/settings").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).detail).toBe("Can't reach AdScout. Check that it's still running.");
  });

  it("never shows a non-JSON error body", async () => {
    const html = "<html><body><h1>502 Bad Gateway</h1></body></html>";
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(html, { status: 502, headers: { "Content-Type": "text/html" } }),
        ),
    );
    const error: unknown = await api.get("/api/settings").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(502);
    expect((error as ApiError).detail).toBe("Request failed (502).");
  });

  it("keeps the API's JSON detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: "Snapshot 9 not found." }), { status: 404 }),
        ),
    );
    const error: unknown = await api.get("/api/snapshots/9/report").catch((e: unknown) => e);
    expect((error as ApiError).detail).toBe("Snapshot 9 not found.");
  });

  it("treats a body that fails to read as a lost connection", async () => {
    const response = new Response("ok", { status: 200 });
    vi.spyOn(response, "text").mockRejectedValue(new TypeError("network error"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const error: unknown = await api.get("/api/settings").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).detail).toBe("Can't reach AdScout. Check that it's still running.");
  });
});
