import { useSettings } from "@/api/hooks";

/** The banner, every disabled control's tooltip, and the server's 403 detail. */
export const DEMO_COPY = "Demo data. Nothing here is real. Run uv run adscout to search for real.";

/** Whether the server is `adscout demo`: sample data, live actions off. */
export function useDemo(): boolean {
  return useSettings().data?.demo === true;
}
