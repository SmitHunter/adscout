import type { components } from "./schema";

/** Response and request shapes, generated from openapi.json (`npm run gen:api`). */
export type Schemas = components["schemas"];
export type Settings = Schemas["SettingsView"];
export type Snapshot = Schemas["SnapshotView"];
export type SearchQuery = Schemas["SearchQuery"];
export type SearchEvent = Schemas["SearchEvent"];
export type StartSearchResponse = Schemas["StartSearchResponse"];
export type CancelResponse = Schemas["CancelResponse"];
export type Report = Schemas["Report"];
export type OverrideBody = Schemas["OverrideBody"];
export type ClearOverrideBody = Schemas["ClearOverrideBody"];
export type SettingsUpdate = Schemas["SettingsUpdate"];
export type AnalysisJob = Schemas["AnalysisJob"];
export type AnalysisView = Schemas["AnalysisView"];
export type WatchItem = Schemas["WatchItemView"];
export type WatchTrend = Schemas["WatchTrend"];
export type TrendPoint = Schemas["TrendPoint"];
export type WatchAlert = Schemas["AlertView"];
export type RunNowResponse = Schemas["RunNowResponse"];
export type MarkReadBody = Schemas["MarkReadBody"];
export type MarkReadResponse = Schemas["MarkReadResponse"];
export type CompareColumn = Schemas["CompareColumn"];

/** A non-2xx response. `detail` is the API's message, ready to show as-is. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function detailFrom(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    // FastAPI validation errors: [{loc, msg, type}, ...]
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item: unknown) =>
          typeof item === "object" && item !== null && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : null,
        )
        .filter((msg): msg is string => msg !== null);
      if (messages.length > 0) return messages.join("; ");
    }
  }
  return fallback;
}

const UNREACHABLE = "Can't reach AdScout. Check that it's still running.";

/** The body as JSON; `undefined` when empty, `NOT_JSON` when it isn't JSON (an HTML page). */
const NOT_JSON = Symbol("not-json");

function parseBody(text: string): unknown {
  if (text.length === 0) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return NOT_JSON;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  let text: string;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    // Reading the body can fail too: the connection dropped mid-response.
    text = await response.text();
  } catch {
    throw new ApiError(0, UNREACHABLE);
  }

  const parsed = parseBody(text);
  if (!response.ok) {
    // A proxy or crash page is never shown raw: only the API's own JSON detail is.
    throw new ApiError(response.status, detailFrom(parsed, `Request failed (${response.status}).`));
  }
  if (parsed === NOT_JSON) throw new ApiError(response.status, "AdScout sent an unreadable reply.");
  return parsed as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string, body?: unknown) => request<T>("DELETE", path, body),
};

export function mediaUrl(snapshotId: number, filename: string): string {
  return `/media/${snapshotId}/${encodeURIComponent(filename)}`;
}
