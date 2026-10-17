import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import type { Report, Settings } from "@/api/client";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DEMO_COPY } from "@/features/demo/demo";

import { confirmCopy, creativesToSend, NO_RELEVANT_COPY, UNAVAILABLE_COPY } from "./analysis";

interface AnalyzeButtonProps {
  report: Report;
  /** Undefined while settings load: the button waits, disabled. */
  settings: Settings | undefined;
  /** `Re-run analysis` (forced) instead of `Analyze angles`. */
  rerun: boolean;
  /** A job is starting or running. */
  busy: boolean;
  onStart: (force: boolean) => void;
  variant?: "default" | "outline";
  size?: "default" | "sm";
}

/** Why analysis can't run here, or null when it can. */
function analyzeBlocker(report: Report, settings: Settings | undefined): ReactNode {
  if (settings === undefined) return null;
  if (settings.demo) return DEMO_COPY;
  if (!settings.analysis.available) {
    return (
      <>
        {settings.analysis.reason ?? UNAVAILABLE_COPY}{" "}
        <Link to="/settings" className="text-signal underline-offset-4 hover:underline">
          Open settings
        </Link>
      </>
    );
  }
  if (report.score.relevant_ads === 0) return NO_RELEVANT_COPY;
  return null;
}

/**
 * `Analyze angles` / `Re-run analysis`. Asks first, since a run spends the
 * user's Claude quota; disabled with the reason in a tooltip when it can't run.
 */
export function AnalyzeButton({
  report,
  settings,
  rerun,
  busy,
  onStart,
  variant = "default",
  size = "default",
}: AnalyzeButtonProps) {
  const [open, setOpen] = useState(false);
  const label = rerun ? "Re-run analysis" : "Analyze angles";
  const blocker = analyzeBlocker(report, settings);
  const disabled = busy || settings === undefined || blocker !== null;

  const button = (
    <Button variant={variant} size={size} disabled={disabled} onClick={() => setOpen(true)}>
      {label}
    </Button>
  );

  return (
    <>
      {blocker !== null ? (
        <Tooltip>
          <TooltipTrigger asChild>
            {/* Disabled buttons swallow pointer events; the wrapper carries the tooltip. */}
            <span tabIndex={0} className="inline-flex rounded-sm">
              {button}
            </span>
          </TooltipTrigger>
          <TooltipContent className="text-pretty">{blocker}</TooltipContent>
        </Tooltip>
      ) : (
        button
      )}

      <AnalyzeConfirm
        open={open}
        onOpenChange={setOpen}
        report={report}
        settings={settings}
        rerun={rerun}
        onConfirm={onStart}
      />
    </>
  );
}

interface AnalyzeConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  report: Report;
  settings: Settings | undefined;
  /** Forced re-run (`Re-run analysis`) or a first run (`Analyze angles`). */
  rerun: boolean;
  onConfirm: (force: boolean) => void;
}

/**
 * The quota check every run passes through: the Analyze button and each
 * `Try again` open this same dialog.
 */
export function AnalyzeConfirm({
  open,
  onOpenChange,
  report,
  settings,
  rerun,
  onConfirm,
}: AnalyzeConfirmProps) {
  const label = rerun ? "Re-run analysis" : "Analyze angles";
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogTitle>{label}</AlertDialogTitle>
        <AlertDialogDescription>
          {confirmCopy(creativesToSend(report), settings?.analysis.backend)}
        </AlertDialogDescription>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={() => onConfirm(rerun)}>{label}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
