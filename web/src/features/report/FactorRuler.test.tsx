import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { FactorPanel, FactorRuler } from "./FactorRuler";
import { makeFactor, makeReport } from "./fixtures";
import { layoutLabels, rulerMax, rulerOverflows } from "./ruler";

describe("FactorRuler", () => {
  it("ruler places marker proportionally and colours by status", () => {
    // Track spans 0..max(full 180 * 1.25, pass 90 * 1.5) = 225.
    render(
      <FactorRuler
        factor={makeFactor({ value: 60, display: "60 days", status: "warn", points: 12 })}
      />,
    );

    const marker = screen.getByTestId("ruler-marker");
    expect(marker.style.getPropertyValue("--at")).toBe("26.667%");
    expect(marker).toHaveClass("left-[clamp(6px,var(--at),calc(100%-6px))]");
    expect(marker).toHaveAttribute("data-status", "warn");
    // The pointer and needle paint in currentColor: the status colour.
    expect(marker).toHaveClass("text-warn");

    const ticks = screen.getAllByTestId("ruler-tick");
    expect(ticks.map((tick) => tick.style.left)).toEqual(["13.333%", "40%", "80%"]);
    // Threshold bands: 0–30 fail, 30–90 warn, 90–225 pass.
    expect(screen.getAllByTestId("ruler-zone").map((zone) => zone.style.width)).toEqual([
      "13.333%",
      "26.667%",
      "60%",
    ]);
    expect(screen.getByTestId("ruler-track")).toHaveAttribute("data-overflow", "false");
    expect(screen.queryByTestId("ruler-overflow")).toBeNull();
    expect(screen.getByText(/fail </, { selector: "span" }).textContent).toBe("fail < 30");
    expect(screen.getByText(/pass ≥/, { selector: "span" }).textContent).toBe("pass ≥ 90");
    expect(screen.getByText(/full/, { selector: "span" }).textContent).toBe("full 180");
    expect(screen.getByTestId("ruler-points").textContent).toBe("12/30");
  });

  it("keeps a fixed range so thresholds stay legible", () => {
    expect(rulerMax(makeFactor({ value: 942 }))).toBe(225);
    expect(rulerMax(makeFactor({ value: 6, threshold: { fail: 3, pass: 5, full: null } }))).toBe(
      7.5,
    );
    // pass * 1.5 wins when full sits close to pass.
    expect(rulerMax(makeFactor({ threshold: { fail: 3, pass: 10, full: 11 } }))).toBe(15);
  });

  it("pins a value past the range to the end, under an overflow arrow", () => {
    const factor = makeFactor({ value: 942, display: "942 days", status: "pass", points: 30 });
    expect(rulerOverflows(factor)).toBe(true);
    render(<FactorRuler factor={factor} />);

    expect(screen.getByTestId("ruler-track")).toHaveAttribute("data-overflow", "true");
    const marker = screen.getByTestId("ruler-marker");
    expect(marker.style.getPropertyValue("--at")).toBe("100%");
    expect(marker).toHaveClass("left-(--at)");
    expect(screen.getByTestId("ruler-overflow")).toHaveClass("text-pass");
    // Ticks keep their places on the capped scale.
    expect(screen.getAllByTestId("ruler-tick").map((tick) => tick.style.left)).toEqual([
      "13.333%",
      "40%",
      "80%",
    ]);
    expect(screen.getByText(/beyond the end of the scale/)).toBeInTheDocument();
  });

  it("pending factor shows hatched track", () => {
    render(
      <FactorRuler
        factor={makeFactor({
          key: "angles",
          label: "Angle diversity",
          value: null,
          display: "—",
          status: "pending",
          points: null,
          max_points: 25,
          detail: "Needs angle analysis",
        })}
      />,
    );

    expect(screen.getByTestId("ruler-track")).toHaveAttribute("data-hatched", "true");
    expect(screen.queryByTestId("ruler-marker")).toBeNull();
    expect(screen.getByText("Needs angle analysis")).toBeInTheDocument();
    expect(screen.getByTestId("ruler-points").textContent).toBe("—/25");
  });

  it("pushes crowded threshold labels apart without leaving the track", () => {
    // Ticks 16 px and 50 px apart; labels 60 px wide.
    expect(layoutLabels([16, 50, 99], [60, 60, 56], 560, 8)).toEqual([16, 84, 152]);
    // The last label is pulled back inside the right edge.
    expect(layoutLabels([500, 560], [60, 50], 560, 8)).toEqual([442, 510]);
  });
});

describe("FactorPanel", () => {
  it("opens on the leader's factors, which the headline score is built from", async () => {
    const { score } = makeReport();
    render(<FactorPanel score={score} />);

    expect(screen.getByRole("radio", { name: "Northbrace" })).toHaveAttribute("data-state", "on");
    // The fixture's leader has 260 days; the market has 120.
    expect(screen.getByText("260")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("radio", { name: "Market" }));
    expect(screen.getByText("120")).toBeInTheDocument();
    expect(screen.queryByText("260")).toBeNull();
  });

  it("shows the market alone when no advertiser leads", () => {
    const { score } = makeReport();
    render(<FactorPanel score={{ ...score, leader: null }} />);
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.getByText("Market")).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
  });
});
