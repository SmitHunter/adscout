import type { SearchQuery } from "@/api/client";

export type SearchForm = Required<
  Pick<SearchQuery, "keyword" | "country" | "active_status" | "media_type">
>;

export const DEFAULT_FORM: SearchForm = {
  keyword: "",
  country: "US",
  active_status: "active",
  media_type: "all",
};

const LOGIN_REASON = "Meta asked for login.";

/**
 * Split a blocked message ("{reason} {how to fix}") into its two parts. Login
 * walls lead with the plain "Meta asked for login." rather than the source's
 * terse "Login required."
 */
export function splitBlocked(message: string | null | undefined): {
  reason: string;
  fix: string;
} {
  const text = (message ?? "").trim();
  if (text === "") {
    return {
      reason: "Meta blocked this search.",
      fix: "Open the browser, check that the Ad Library loads, then search again.",
    };
  }
  const match = /^(.+?[.!?])\s+([\s\S]*)$/.exec(text);
  // Both parts go through forTheApp, so neither can show a CLI command.
  if (match === null) return { reason: forTheApp(text), fix: "" };
  const reason = forTheApp(match[1] ?? text);
  const fix = forTheApp(match[2] ?? "");
  if (fix.startsWith(LOGIN_REASON)) {
    return { reason: LOGIN_REASON, fix: fix.slice(LOGIN_REASON.length).trim() };
  }
  return { reason, fix };
}

/** Shown in place of any fix sentence that still names a CLI command. */
export const GENERIC_FIX = "Open the browser, complete the sign-in or check yourself, then retry.";

/**
 * The sources write their fixes for the CLI ("Run: uv run adscout browser").
 * Here the Open browser button does that, so point at it instead. The known
 * wordings live in src/adscout/sources/meta_web/blocks.py (pinned by
 * tests/sources/meta_web/test_blocks.py); if they drift, any sentence that
 * still mentions `uv run` falls back to GENERIC_FIX, so the GUI never shows a
 * CLI command.
 */
export function forTheApp(fix: string): string {
  const mapped = fix.replace(
    /(?:Run|Open the browser with): uv run adscout browser\s*[,—-]\s*/,
    "Open the browser, ",
  );
  const sentences = mapped.split(/(?<=[.!?])\s+/).filter((s) => s !== "");
  const safe = sentences.map((sentence) => (sentence.includes("uv run") ? GENERIC_FIX : sentence));
  return safe.filter((sentence, index) => safe.indexOf(sentence) === index).join(" ");
}
