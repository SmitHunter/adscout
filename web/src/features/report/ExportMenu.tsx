import { useState } from "react";
import { ChevronDownIcon, DownloadIcon, LoaderCircleIcon } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { download, FORMATS, type ExportFormat } from "./download";

/**
 * The report header's Export menu: CSV, PDF or PNG. PDF and PNG are rendered
 * by a headless browser and take a few seconds, so each format shows its own
 * pending state; failures (another export running, a timeout) arrive as toasts.
 */
export function ExportMenu({ snapshotId }: { snapshotId: number }) {
  const [pending, setPending] = useState<ExportFormat[]>([]);
  const busy = pending.length > 0;
  const latest = pending[pending.length - 1];

  const run = (format: ExportFormat) => {
    if (pending.includes(format)) return;
    setPending((current) => [...current, format]);
    download(snapshotId, format)
      .catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : String(error));
      })
      .finally(() => setPending((current) => current.filter((f) => f !== format)));
  };

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <Button variant="outline" aria-busy={busy || undefined}>
          {busy ? (
            <LoaderCircleIcon
              className="animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
          ) : (
            <DownloadIcon aria-hidden="true" />
          )}
          {latest ? `Exporting ${latest.toUpperCase()}` : "Export"}
          <ChevronDownIcon className="-mr-1 text-muted" aria-hidden="true" />
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 min-w-48 border border-rule bg-panel py-1 text-text shadow-popover outline-none"
        >
          {FORMATS.map((format) => {
            const running = pending.includes(format);
            const label = format.toUpperCase();
            return (
              <DropdownMenu.Item
                key={format}
                disabled={running}
                onSelect={() => run(format)}
                className={cn(
                  "flex h-8 cursor-pointer items-center gap-2 px-3 text-13 outline-none select-none",
                  "data-highlighted:bg-rule/60 data-disabled:cursor-default data-disabled:text-muted",
                )}
              >
                {running ? (
                  <LoaderCircleIcon
                    className="size-4 animate-spin text-muted motion-reduce:animate-none"
                    aria-hidden="true"
                  />
                ) : (
                  <DownloadIcon className="size-4 text-muted" aria-hidden="true" />
                )}
                {running ? `Preparing ${label}` : `Download ${label}`}
              </DropdownMenu.Item>
            );
          })}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
