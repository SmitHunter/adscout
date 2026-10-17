import * as React from "react";
import { Toaster as Sonner, type ToasterProps } from "sonner";

import { useTheme } from "@/app/theme";

const TONES = ["success", "info", "warning", "error"] as const;
const TONE_TOKEN: Record<(typeof TONES)[number], string> = {
  success: "var(--pass)",
  info: "var(--signal)",
  warning: "var(--warn)",
  error: "var(--fail)",
};
/** Sonner's grey ramp (light to dark) on the app's surfaces and text. */
const GRAYS = [
  "var(--panel)",
  "var(--panel)",
  "var(--rule)",
  "var(--rule)",
  "var(--rule)",
  "var(--rule)",
  "var(--rule)",
  "var(--muted)",
  "var(--muted)",
  "var(--muted)",
  "var(--muted)",
  "var(--text)",
];

/**
 * Every colour variable sonner's stylesheet reads, mapped to a design token, so no
 * default sonner colour (white panels, its own greens and reds) ever shows.
 */
const TOAST_STYLE: Record<string, string> = {
  "--normal-bg": "var(--panel)",
  "--normal-bg-hover": "var(--rule)",
  "--normal-text": "var(--text)",
  "--normal-border": "var(--rule)",
  "--normal-border-hover": "var(--muted)",
  "--border-radius": "0px",
  ...Object.fromEntries(
    TONES.flatMap((tone) => [
      [`--${tone}-bg`, "var(--panel)"],
      [`--${tone}-border`, "var(--rule)"],
      [`--${tone}-text`, TONE_TOKEN[tone]],
    ]),
  ),
  ...Object.fromEntries(GRAYS.map((value, i) => [`--gray${i + 1}`, value])),
};

/* Toasts are popovers: panel surface, 1px rule, the one allowed shadow. */
function Toaster(props: ToasterProps) {
  const { theme } = useTheme();
  return (
    <Sonner
      theme={theme}
      position="bottom-right"
      className="toaster group"
      toastOptions={{
        classNames: {
          toast: "!font-ui !text-13 !rounded-none !shadow-popover",
          title: "!text-text",
          description: "!text-muted",
          actionButton: "!rounded-sm !bg-signal !text-bg",
          cancelButton: "!rounded-sm !bg-rule !text-text",
          closeButton:
            "!border-rule !bg-panel !text-muted hover:!text-text focus-visible:!shadow-none focus-visible:!outline-2 focus-visible:!outline-signal",
          error: "!border-l-2 !border-l-fail [&_[data-icon]]:!text-fail",
          success: "!border-l-2 !border-l-pass [&_[data-icon]]:!text-pass",
          warning: "!border-l-2 !border-l-warn [&_[data-icon]]:!text-warn",
          info: "!border-l-2 !border-l-signal [&_[data-icon]]:!text-signal",
        },
      }}
      style={TOAST_STYLE as React.CSSProperties}
      {...props}
    />
  );
}

export { Toaster };
