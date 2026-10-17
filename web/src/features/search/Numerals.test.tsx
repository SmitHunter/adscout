import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DisplayInt, Numerals } from "./Numerals";

function numeralRuns(text: string): string[] {
  const { container } = render(
    <p>
      <Numerals text={text} />
    </p>,
  );
  return Array.from(container.querySelectorAll(".num")).map((el) => el.textContent ?? "");
}

describe("Numerals", () => {
  it("sets standalone numbers in the numeral face", () => {
    expect(numeralRuns("43% of creatives running ≥ 90 days")).toEqual(["43", "90"]);
    expect(numeralRuns("Found 1,284 ads at 10:30")).toEqual(["1,284", "10:30"]);
  });

  it("leaves digits that follow a letter inside the word", () => {
    expect(numeralRuns("Needs Claude angle analysis (M4)")).toEqual([]);
    expect(numeralRuns("Arrives in M42, not M5")).toEqual([]);
    expect(numeralRuns("Built with v0.1.0")).toEqual([]);
  });
});

describe("DisplayInt", () => {
  it("keeps the number intact and narrows only its separators", () => {
    const { container } = render(<DisplayInt text="1,284,000" className="num" />);
    const root = container.firstElementChild;
    expect(root?.textContent).toBe("1,284,000");
    expect(root).toHaveClass("num");
    const seps = Array.from(container.querySelectorAll("[data-sep]"));
    expect(seps.map((el) => el.textContent)).toEqual([",", ","]);
  });

  it("leaves a short number alone", () => {
    const { container } = render(<DisplayInt text="60" />);
    expect(container.querySelectorAll("[data-sep]")).toHaveLength(0);
    expect(container.textContent).toBe("60");
  });
});
