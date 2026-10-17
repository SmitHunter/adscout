import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { ToggleGroup as ToggleGroupPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

const toggleGroupItemVariants = cva(
  "inline-flex cursor-pointer items-center justify-center gap-1.5 border-l border-rule font-medium whitespace-nowrap text-muted transition-colors first:border-l-0 hover:text-text focus-visible:-outline-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[state=on]:bg-rule data-[state=on]:text-text [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
  {
    variants: {
      size: {
        default: "h-[30px] px-3 text-13",
        sm: "h-[26px] px-2 text-12",
      },
    },
    defaultVariants: { size: "default" },
  },
);

type Size = VariantProps<typeof toggleGroupItemVariants>["size"];

const ToggleGroupContext = React.createContext<Size>("default");

function ToggleGroup({
  className,
  size,
  children,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Root> & { size?: Size }) {
  return (
    <ToggleGroupPrimitive.Root
      data-slot="toggle-group"
      data-size={size}
      className={cn(
        "inline-flex w-fit items-stretch rounded-sm border border-rule bg-panel",
        className,
      )}
      {...props}
    >
      <ToggleGroupContext.Provider value={size}>{children}</ToggleGroupContext.Provider>
    </ToggleGroupPrimitive.Root>
  );
}

function ToggleGroupItem({
  className,
  children,
  size,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Item> & { size?: Size }) {
  const groupSize = React.useContext(ToggleGroupContext);
  return (
    <ToggleGroupPrimitive.Item
      data-slot="toggle-group-item"
      className={cn(toggleGroupItemVariants({ size: size ?? groupSize }), className)}
      {...props}
    >
      {children}
    </ToggleGroupPrimitive.Item>
  );
}

export { ToggleGroup, ToggleGroupItem };
