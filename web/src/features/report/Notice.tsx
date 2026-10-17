import type { ReactNode } from "react";

/** A report-level caveat (partial data, nothing found), with an optional action. */
export function Notice({
  text,
  detail,
  tone = "warn",
  children,
}: {
  text: string;
  /** `warn` for partial or missing data; `neutral` for an expected stop like the ad limit. */
  tone?: "warn" | "neutral";
  /** A second, quieter line: why the run stopped. */
  detail?: string;
  children?: ReactNode;
}) {
  return (
    <div
      role="status"
      className={`flex flex-col gap-3 border border-rule ${tone === "warn" ? "border-l-warn" : "border-l-muted"} bg-panel py-2.5 pr-3 pl-4 [border-left-width:2px] sm:flex-row sm:items-center sm:justify-between`}
    >
      <div className="space-y-0.5">
        <p className="text-13 text-text">{text}</p>
        {detail ? <p className="text-13 text-muted">{detail}</p> : null}
      </div>
      {children ? <div className="shrink-0">{children}</div> : null}
    </div>
  );
}
