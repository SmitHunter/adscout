import { DEMO_COPY, useDemo } from "./demo";

/** The slim strip above everything while the demo runs. */
export function DemoBanner() {
  if (!useDemo()) return null;
  return (
    <div
      role="note"
      className="flex min-h-8 items-center gap-2.5 border-b border-warn/40 bg-warn/10 px-4 py-1.5 text-13 text-text md:px-10"
    >
      <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-warn" />
      <p>{DEMO_COPY}</p>
    </div>
  );
}
