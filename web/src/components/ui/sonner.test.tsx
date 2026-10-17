import { act, render, screen } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { Toaster } from "./sonner";

/** Every colour variable sonner's own stylesheet reads. */
const SONNER_COLOR_VARS = [
  "--normal-bg",
  "--normal-bg-hover",
  "--normal-text",
  "--normal-border",
  "--normal-border-hover",
  ...["success", "info", "warning", "error"].flatMap((tone) => [
    `--${tone}-bg`,
    `--${tone}-border`,
    `--${tone}-text`,
  ]),
  ...Array.from({ length: 12 }, (_, i) => `--gray${i + 1}`),
];

afterEach(() => {
  act(() => toast.dismiss());
});

describe("Toaster", () => {
  it("maps every sonner colour to a design token", async () => {
    render(<Toaster />);
    act(() => {
      toast.success("Settings saved");
    });
    await screen.findByText("Settings saved");
    const toaster = document.querySelector<HTMLElement>("[data-sonner-toaster]");
    expect(toaster).not.toBeNull();
    for (const name of SONNER_COLOR_VARS) {
      expect(toaster!.style.getPropertyValue(name), name).toMatch(/^var\(--[a-z]+\)$/);
    }
    expect(toaster!.style.getPropertyValue("--border-radius")).toBe("0px");
  });
});
