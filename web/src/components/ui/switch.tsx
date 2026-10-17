import * as React from "react";
import { Switch as SwitchPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

/* A 2px-radius track with a square thumb, matching the other controls. */
function Switch({ className, ...props }: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-sm border border-rule bg-bg p-0.5 transition-colors hover:border-muted disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-signal data-[state=checked]:bg-signal/20",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="block size-3.5 rounded-[1px] bg-muted transition-transform data-[state=checked]:translate-x-4 data-[state=checked]:bg-signal" />
    </SwitchPrimitive.Root>
  );
}

export { Switch };
