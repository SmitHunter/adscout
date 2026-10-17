import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { makeAd, makeAnalysis, makeReport, makeSettings } from "./fixtures";
import { renderReport, reportGets, serveReport } from "./testing";

afterEach(() => vi.unstubAllGlobals());

const OFF_TOPIC = ["2001", "2002", "2003"].map((id, i) =>
  makeAd({
    ad_archive_id: id,
    relevant: false,
    reason: "claude_off_topic",
    creative_key: i < 2 ? "c-massage" : "c-brand",
    page_name: "Massage World",
    advertiser_key: "777",
    page_id: "777",
    body: "Posture corrector not working? Try our massage gun.",
  }),
);

function analysedReport(flagged = 0) {
  return makeReport({
    analysis: makeAnalysis({ flagged_off_topic: flagged }),
    ads: [
      makeAd({
        ad_archive_id: "1001",
        body: "Sitting all day wrecks your back. Fix it in 14 days.",
      }),
      makeAd({ ad_archive_id: "1002", body: "Back pain from your desk job? Not any more." }),
      makeAd({
        ad_archive_id: "1004",
        body: "Stand tall and walk into every room like you own it.",
      }),
      ...OFF_TOPIC.slice(0, flagged),
    ],
  });
}

describe("AnglesPanel", () => {
  it("angles panel renders verdict clusters offers", async () => {
    serveReport(() => analysedReport(), undefined, { settings: makeSettings() });
    renderReport();
    const user = userEvent.setup();

    const panel = await screen.findByRole("region", { name: "Angles" });
    expect(within(panel).getByTestId("analysis-verdict")).toHaveTextContent(
      /^Northbrace dominates with pain-relief copy/,
    );
    expect(within(panel).getByText("Claude Sonnet 5.5")).toBeInTheDocument();

    // Sorted by live creatives, not by the stored id lists.
    const clusters = within(panel).getAllByTestId("angle-cluster");
    expect(clusters.map((c) => within(c).getByRole("heading").textContent)).toEqual([
      "Look taller and confident",
      "Desk-job back pain",
      "Gift for parents",
    ]);
    expect(within(clusters[0]!).getByText("Confidence")).toBeInTheDocument();
    expect(within(clusters[0]!).getByTestId("angle-count")).toHaveTextContent("9 creatives");
    expect(within(clusters[0]!).getByTestId("angle-bar")).toHaveStyle({ width: "100%" });
    expect(within(clusters[1]!).getByTestId("angle-bar")).toHaveStyle({ width: "33.33%" });
    expect(within(clusters[2]!).getByText("Not scored")).toBeInTheDocument();

    // Two example copies from the angle's own ads, on request.
    await user.click(within(clusters[1]!).getByRole("button", { name: "Show examples" }));
    expect(within(clusters[1]!).getByText(/Sitting all day wrecks your back/)).toBeInTheDocument();
    expect(within(clusters[1]!).getByText(/Back pain from your desk job/)).toBeInTheDocument();

    expect(within(panel).getByText("“Your back shouldn't hurt at 30.”")).toBeInTheDocument();
    const discounts = within(panel).getByRole("list", { name: "Discount" });
    expect(
      within(discounts)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["50% off today", "Buy 2 get 1 free"]);
    expect(within(panel).getByRole("list", { name: "Free shipping" })).toBeInTheDocument();
    expect(within(panel).getByText("Evergreen")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Re-run analysis" })).toBeEnabled();
    expect(within(panel).queryByRole("button", { name: /off-topic/ })).toBeNull();
  });

  it("off-topic ads link to the excluded tab, where include sets an ad include", async () => {
    const calls = serveReport(() => analysedReport(3), undefined, { settings: makeSettings() });
    renderReport();
    const user = userEvent.setup();

    const panel = await screen.findByRole("region", { name: "Angles" });
    expect(within(panel).queryByRole("button", { name: /^Exclude/ })).toBeNull();
    const link = within(panel).getByRole("button", { name: "3 ads marked off-topic" });
    await user.hover(link);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Claude flagged 2 creatives (3 ads) as not selling this product.",
    );
    await user.click(link);

    const excludedTab = screen.getByRole("tab", { name: "Excluded (2)" });
    expect(excludedTab).toHaveAttribute("data-state", "active");
    const tabPanel = screen.getByRole("tabpanel");
    const chips = within(tabPanel).getAllByText("Claude: off-topic");
    expect(chips).toHaveLength(2);

    const tile = chips[0]!.closest<HTMLElement>('[data-testid="creative-tile"]')!;
    await user.click(within(tile).getByRole("button", { name: /^Include/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual(
      ["2001", "2002"].map((id) => ({
        snapshot_id: 1,
        scope: "ad",
        target_id: id,
        mode: "include",
      })),
    );
    await waitFor(() => expect(reportGets(calls)).toBe(2));
  });

  it("no off-topic link when nothing is flagged", async () => {
    serveReport(() => analysedReport(0), undefined, { settings: makeSettings() });
    renderReport();
    const panel = await screen.findByRole("region", { name: "Angles" });
    expect(within(panel).queryByText(/marked off-topic/)).toBeNull();
  });
});
