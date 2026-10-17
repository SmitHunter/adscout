import type { ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { DEMO_COPY } from "./demo";

/**
 * Wraps a control the demo switches off: when `locked`, the (already disabled)
 * child explains why on hover and keyboard focus. A disabled button gets no
 * pointer events, so the focusable wrapper carries the tooltip.
 */
export function DemoLock({ locked, children }: { locked: boolean; children: ReactNode }) {
  if (!locked) return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          data-testid="demo-lock"
          aria-label={DEMO_COPY}
          className="inline-flex cursor-not-allowed rounded-sm focus-visible:outline-offset-2 [&>*]:pointer-events-none"
        >
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent className="text-pretty">{DEMO_COPY}</TooltipContent>
    </Tooltip>
  );
}
