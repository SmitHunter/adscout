import { describe, expect, it } from "vitest";

import { GENERIC_FIX, splitBlocked } from "./form";

describe("splitBlocked", () => {
  it("leads a login wall with the plain reason", () => {
    expect(
      splitBlocked(
        "Login required. Meta asked for login. Run: uv run adscout browser — sign in once, then retry.",
      ),
    ).toEqual({
      reason: "Meta asked for login.",
      fix: "Open the browser, sign in once, then retry.",
    });
  });

  it("keeps the source's own reason otherwise", () => {
    expect(
      splitBlocked(
        "Temporarily blocked. Meta rate-limited this browser. Wait a few hours before searching again.",
      ),
    ).toEqual({
      reason: "Temporarily blocked.",
      fix: "Meta rate-limited this browser. Wait a few hours before searching again.",
    });
  });

  it("points security checks at the Open browser button", () => {
    expect(
      splitBlocked(
        "Security check. Meta showed a security check. Open the browser with: uv run adscout browser, complete it yourself, wait 30+ minutes, then retry.",
      ).fix,
    ).toBe(
      "Meta showed a security check. Open the browser, complete it yourself, wait 30+ minutes, then retry.",
    );
  });

  it("never shows a CLI command when the backend wording drifts", () => {
    const { reason, fix } = splitBlocked(
      "Login required. Your session expired. Run: uv run adscout browser to sign in again.",
    );
    expect(reason).toBe("Login required.");
    expect(fix).toBe(`Your session expired. ${GENERIC_FIX}`);
    expect(fix).not.toContain("uv run");
  });

  it("never shows a CLI command in a one-sentence message", () => {
    const { reason, fix } = splitBlocked("Run uv run adscout browser to sign in");
    expect(reason).toBe(GENERIC_FIX);
    expect(fix).toBe("");
  });

  it("never shows a CLI command in the reason sentence", () => {
    const { reason } = splitBlocked("Run: uv run adscout browser. Then retry.");
    expect(reason).not.toContain("uv run");
  });

  it("explains a block that came without a message", () => {
    expect(splitBlocked(null).reason).toBe("Meta blocked this search.");
  });
});
