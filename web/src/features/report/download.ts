/** Report exports: fetched same-origin, then handed to the browser as a download. */

export type ExportFormat = "csv" | "pdf" | "png";

export const FORMATS: ExportFormat[] = ["csv", "pdf", "png"];

const OFFLINE = "Can't reach AdScout. Check that it's still running.";

/** `attachment; filename="a.pdf"` (or RFC 5987 `filename*=UTF-8''a.pdf`) → `a.pdf`. */
export function filenameFrom(disposition: string | null): string | null {
  if (!disposition) return null;
  const extended = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(disposition);
  if (extended?.[1]) {
    try {
      return decodeURIComponent(extended[1].trim());
    } catch {
      // Malformed escapes: fall through to the plain name.
    }
  }
  const plain = /filename\s*=\s*"([^"]+)"|filename\s*=\s*([^;]+)/.exec(disposition);
  const name = (plain?.[1] ?? plain?.[2])?.trim();
  return name ? name : null;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as unknown;
    if (typeof body === "object" && body !== null && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string" && detail) return detail;
    }
  } catch {
    // Not JSON.
  }
  return `Export failed (HTTP ${response.status}).`;
}

/** Fetches an export and hands it to the browser as a download. Throws the API's message. */
export async function download(snapshotId: number, format: ExportFormat): Promise<void> {
  let response: Response;
  try {
    // Same-origin fetch (not a plain link) so a failure can be shown as a toast.
    response = await fetch(`/api/snapshots/${snapshotId}/export.${format}`);
  } catch {
    throw new Error(OFFLINE);
  }
  if (!response.ok) throw new Error(await errorDetail(response));

  const blob = await response.blob();
  const name =
    filenameFrom(response.headers.get("Content-Disposition")) ?? `adscout-${snapshotId}.${format}`;
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  // Give the browser a moment to start the download before freeing the blob.
  window.setTimeout(() => URL.revokeObjectURL(href), 10_000);
}
