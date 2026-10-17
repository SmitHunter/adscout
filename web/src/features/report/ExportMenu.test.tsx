import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";

import { ExportMenu } from "./ExportMenu";

let clicked: { href: string; download: string }[];

beforeEach(() => {
  clicked = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    clicked.push({ href: this.href, download: this.download });
  });
  URL.createObjectURL = vi.fn(() => "blob:adscout/1");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function serve(answer: (url: string) => Response | Promise<Response>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      return Promise.resolve(answer(url));
    }),
  );
  return calls;
}

async function openMenu(
  user: ReturnType<typeof userEvent.setup>,
  name: string | RegExp = "Export",
) {
  screen.getByRole("button", { name }).focus();
  await user.keyboard("{Enter}");
  return screen.findByRole("menu");
}

function renderMenu() {
  return render(
    <>
      <ExportMenu snapshotId={7} />
      <Toaster />
    </>,
  );
}

describe("ExportMenu", () => {
  it("export menu links", async () => {
    const calls = serve(
      (url) =>
        new Response("a,b\n", {
          headers: {
            "Content-Disposition": `attachment; filename="adscout-posture-corrector-7.${url.split(".").pop()}"`,
          },
        }),
    );
    const user = userEvent.setup();
    renderMenu();

    await openMenu(user);
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Download CSV",
      "Download PDF",
      "Download PNG",
    ]);
    await user.keyboard("{Escape}");

    for (const format of ["csv", "pdf", "png"]) {
      await openMenu(user);
      await user.click(screen.getByRole("menuitem", { name: `Download ${format.toUpperCase()}` }));
      await waitFor(() => expect(calls).toContain(`/api/snapshots/7/export.${format}`));
      await waitFor(() =>
        expect(clicked.at(-1)).toEqual({
          href: "blob:adscout/1",
          download: `adscout-posture-corrector-7.${format}`,
        }),
      );
    }
    expect(URL.createObjectURL).toHaveBeenCalledTimes(3);
  });

  it("shows the pending format and an error toast when the export fails", async () => {
    let release: (response: Response) => void = () => {};
    serve(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    const user = userEvent.setup();
    renderMenu();

    await openMenu(user);
    await user.click(screen.getByRole("menuitem", { name: "Download PDF" }));
    expect(await screen.findByRole("button", { name: "Exporting PDF" })).toBeInTheDocument();

    await openMenu(user, "Exporting PDF");
    expect(screen.getByRole("menuitem", { name: "Preparing PDF" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    await user.keyboard("{Escape}");

    release(
      new Response(JSON.stringify({ detail: "Another export is in progress." }), { status: 409 }),
    );
    expect(await screen.findByText("Another export is in progress.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeEnabled();
    expect(clicked).toHaveLength(0);
  });
});
