import * as React from "react";

import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "h-8 w-full min-w-0 rounded-sm border border-rule bg-panel px-2.5 text-13 text-text transition-colors placeholder:text-muted hover:border-muted focus-visible:border-signal disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-fail",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
