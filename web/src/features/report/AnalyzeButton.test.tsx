import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnalysisJob } from "@/api/client";

import { makeAnalysis, makeJob, makeReport, makeSettings } from "./fixtures";
import { renderReport, reportGets, serveReport } from "./testing";

afterEach(() => vi.unstubAllGlobals());

const UNAVAILABLE =
  "Angle analysis needs Claude. Install Claude Code or set ANTHROPIC_API_KEY in .env, then restart AdScout.";

describe("Analyze angles", () => {
  it("analyze confirms polls and refreshes", async () => {
    let job: AnalysisJob | null = null;
    let polls = 0;
    const calls = serveReport(
      () => (job?.state === "done" ? makeReport({ analysis: makeAnalysis() }) : makeReport()),
      undefined,
      {
        settings: makeSettings(),
        job: () => {
          if (job !== null) {
            polls += 1;
            // The first poll after the start still runs; the next one is done.
            if (polls >= 2) job = makeJob({ state: "done", finished_at: "2026-10-17T05:12:00Z" });
          }
          return job;
        },
        start: () => {
          job = makeJob();
          return { status: 202, body: job };
        },
      },
    );
    renderReport();
    const user = userEvent.setup();

    const panel = await screen.findByRole("region", { name: "Angles" });
    await user.click(await within(panel).findByRole("button", { name: "Analyze angles" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(
      "This sends 65 creatives to Claude (Claude Code) and uses your quota. Continue?",
    );
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Analyze angles" }));

    expect(await screen.findByTestId("analysis-running")).toHaveTextContent(
      "Analyzing 64 creatives with Claude Sonnet 5.5…",
    );
    expect(calls.filter((c) => c.method === "POST").map((c) => [c.url, c.body])).toEqual([
      ["/api/snapshots/1/analysis", { force: false }],
    ]);

    expect(
      await screen.findByTestId("analysis-verdict", undefined, { timeout: 6000 }),
    ).toBeInTheDocument();
    expect(reportGets(calls)).toBe(2);
    expect(screen.queryByTestId("analysis-running")).toBeNull();
  }, 10000);

  it("analyze confirm takes focus, closes on Escape and gives focus back", async () => {
    const calls = serveReport(() => makeReport(), undefined, { settings: makeSettings() });
    renderReport();
    const user = userEvent.setup();

    const panel = await screen.findByRole("region", { name: "Angles" });
    const trigger = await within(panel).findByRole("button", { name: "Analyze angles" });
    await waitFor(() => expect(trigger).toBeEnabled());
    await user.click(trigger);
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(trigger).toHaveFocus();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("unavailable disables analyze with tooltip", async () => {
    serveReport(() => makeReport(), undefined, {
      settings: makeSettings({
        analysis: { available: false, backend: null, reason: UNAVAILABLE },
      }),
    });
    renderReport();
    const user = userEvent.setup();

    const button = () => screen.getByRole("button", { name: "Analyze angles" });
    // Settings arrive after the report: the button is disabled and wrapped then.
    await waitFor(() => expect(button().parentElement).toHaveAttribute("tabindex", "0"));
    expect(button()).toBeDisabled();
    await user.hover(button().parentElement!);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(UNAVAILABLE);
    expect(screen.getAllByRole("link", { name: "Open settings" })[0]).toHaveAttribute(
      "href",
      "/settings",
    );
  });

  it("zero relevant disables analyze", async () => {
    const report = makeReport();
    report.score.relevant_ads = 0;
    report.score.creatives = 0;
    serveReport(() => report, undefined, { settings: makeSettings() });
    renderReport();
    const user = userEvent.setup();

    const button = () => screen.getByRole("button", { name: "Analyze angles" });
    // Settings arrive after the report: the button is disabled and wrapped then.
    await waitFor(() => expect(button().parentElement).toHaveAttribute("tabindex", "0"));
    expect(button()).toBeDisabled();
    await user.hover(button().parentElement!);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("No relevant ads to analyze.");
  });

  it("a failed job shows its message with try again", async () => {
    const calls = serveReport(() => makeReport(), undefined, {
      settings: makeSettings(),
      job: () =>
        makeJob({
          state: "error",
          message: "Claude Code isn't logged in. Run claude once in a terminal to sign in.",
        }),
      start: () => ({ status: 202, body: makeJob() }),
    });
    renderReport();
    const user = userEvent.setup();

    expect(
      await screen.findByText(
        "Claude Code isn't logged in. Run claude once in a terminal to sign in.",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    // Same quota check as Analyze: nothing is sent until the dialog is confirmed.
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("and uses your quota. Continue?");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Analyze angles" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([
        { force: false },
      ]),
    );
  });
});
