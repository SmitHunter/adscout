import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { buttonVariants } from "@/components/ui/button";

interface RemoveWatchDialogProps {
  query: string | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  /** The confirm button: `Remove` on the watchlist, `Stop watching` from a Watching button. */
  action?: string;
}

/** Removing a watch deletes its alerts; its checks stay as ordinary searches. */
export function RemoveWatchDialog({
  query,
  onOpenChange,
  onConfirm,
  action = "Remove",
}: RemoveWatchDialogProps) {
  return (
    <AlertDialog open={query !== null} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogTitle>Stop watching {query}?</AlertDialogTitle>
        <AlertDialogDescription>
          AdScout stops checking it daily and deletes its alerts. Past checks stay in Recent
          searches.
        </AlertDialogDescription>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={buttonVariants({ variant: "destructive" })}
            onClick={onConfirm}
          >
            {action}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
