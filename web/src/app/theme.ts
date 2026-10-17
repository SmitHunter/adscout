import { useCallback, useSyncExternalStore } from "react";

/**
 * Theme lives on <html data-theme>. index.html sets it before first paint from
 * localStorage, or from the system preference on a first visit. Storage access
 * is wrapped because it throws in some private and locked-down browsers.
 */

export type Theme = "dark" | "light";

export const THEME_STORAGE_KEY = "adscout-theme";

/** On <html> while the print page is open: it forces the light theme. */
export const PRINT_ROUTE_CLASS = "print-route";

function isTheme(value: unknown): value is Theme {
  return value === "dark" || value === "light";
}

export function readStoredTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(value) ? value : null;
  } catch {
    return null;
  }
}

function storeTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Not remembered across visits; the switch itself still applies.
  }
}

export function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function currentTheme(): Theme {
  const value = document.documentElement.getAttribute("data-theme");
  return isTheme(value) ? value : "dark";
}

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  storeTheme(theme);
  for (const listener of listeners) listener();
}

/** Until the user picks a theme, keep matching the system as it changes. */
export function followSystemTheme(): () => void {
  const query = window.matchMedia?.("(prefers-color-scheme: light)");
  if (!query) return () => {};
  const onChange = () => {
    if (readStoredTheme() !== null) return;
    // The print page stays light whatever the system says.
    if (document.documentElement.classList.contains(PRINT_ROUTE_CLASS)) return;
    document.documentElement.setAttribute("data-theme", query.matches ? "light" : "dark");
    for (const listener of listeners) listener();
  };
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

export function useTheme(): { theme: Theme; setTheme: (theme: Theme) => void } {
  const theme = useSyncExternalStore(subscribe, currentTheme, () => "dark" as const);
  const setTheme = useCallback((next: Theme) => applyTheme(next), []);
  return { theme, setTheme };
}
