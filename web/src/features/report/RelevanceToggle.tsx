import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface RelevanceToggleProps {
  /** True when the target is out of the score: the button then reads `Include`. */
  excluded: boolean;
  /** Who the button acts on, for screen readers: `ad from Northbrace`, `Northbrace`. */
  subject: string;
  pending: boolean;
  onToggle: () => void;
  className?: string;
}

/** `Exclude` / `Include`: takes an ad or advertiser out of the score, or puts it back. */
export function RelevanceToggle({
  excluded,
  subject,
  pending,
  onToggle,
  className,
}: RelevanceToggleProps) {
  const verb = excluded ? "Include" : "Exclude";
  return (
    <Button
      type="button"
      size="sm"
      variant={excluded ? "outline" : "ghost"}
      disabled={pending}
      aria-busy={pending || undefined}
      // Starts with the visible verb, so voice control still matches it.
      aria-label={`${verb} ${subject}`}
      onClick={onToggle}
      className={cn(
        excluded ? "border-signal/60 text-signal hover:border-signal" : "hover:text-fail",
        className,
      )}
    >
      {verb}
    </Button>
  );
}
