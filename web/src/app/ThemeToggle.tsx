import { MoonIcon, SunIcon } from "lucide-react";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

import { useTheme, type Theme } from "./theme";

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();

  return (
    <ToggleGroup
      type="single"
      size="sm"
      value={theme}
      aria-label="Theme"
      className={cn("grid w-full grid-cols-2", className)}
      onValueChange={(value) => {
        // Radix sends "" when the active item is clicked again; keep the theme.
        if (value === "dark" || value === "light") setTheme(value satisfies Theme);
      }}
    >
      <ToggleGroupItem value="dark">
        <MoonIcon aria-hidden="true" />
        Dark
      </ToggleGroupItem>
      <ToggleGroupItem value="light">
        <SunIcon aria-hidden="true" />
        Light
      </ToggleGroupItem>
    </ToggleGroup>
  );
}
