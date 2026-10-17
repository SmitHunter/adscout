import { useId, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { ApiError, type Settings, type SettingsUpdate } from "@/api/client";
import { useSettings, useUpdateSettings } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { backendLabel, modelLabel, UNAVAILABLE_COPY } from "@/features/report/analysis";
import { cn } from "@/lib/utils";

type Backend = Settings["analysis_backend"];
type Model = Settings["analysis_model"];

const BACKENDS: { value: Backend; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "cli", label: "Claude Code" },
  { value: "api", label: "Anthropic API" },
];

const MODELS: Model[] = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"];

interface Draft {
  analysis_backend: Backend;
  analysis_model: Model;
  auto_analyze: boolean;
  max_results: string;
  scroll_delay_min: string;
  scroll_delay_max: string;
  daily_check_time: string;
  desktop_notifications: boolean;
  max_auto_analyses_per_day: string;
}

type Field = "limit" | "scroll" | "time" | "autoCap" | "form";

/** The tooltip on the notifications toggle when the optional extra is missing. */
const NOTIFY_INSTALL_COPY = "Install with: uv sync --extra notify";

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
type Errors = Partial<Record<Field, string>>;

function draftOf(s: Settings): Draft {
  return {
    analysis_backend: s.analysis_backend,
    analysis_model: s.analysis_model,
    auto_analyze: s.auto_analyze,
    max_results: String(s.max_results),
    scroll_delay_min: String(s.scroll_delay_min),
    scroll_delay_max: String(s.scroll_delay_max),
    daily_check_time: s.daily_check_time,
    desktop_notifications: s.desktop_notifications,
    max_auto_analyses_per_day: String(s.max_auto_analyses_per_day),
  };
}

/** The fields that changed, as numbers where the API wants them; or the inputs that aren't numbers. */
function patchOf(draft: Draft, saved: Settings): { patch: SettingsUpdate; errors: Errors } {
  const patch: SettingsUpdate = {};
  const errors: Errors = {};
  if (draft.analysis_backend !== saved.analysis_backend) {
    patch.analysis_backend = draft.analysis_backend;
  }
  if (draft.analysis_model !== saved.analysis_model) patch.analysis_model = draft.analysis_model;
  if (draft.auto_analyze !== saved.auto_analyze) patch.auto_analyze = draft.auto_analyze;

  const limit = Number(draft.max_results);
  if (draft.max_results.trim() === "" || !Number.isInteger(limit)) {
    errors.limit = "Enter a whole number of ads.";
  } else if (limit !== saved.max_results) patch.max_results = limit;

  const lo = Number(draft.scroll_delay_min);
  const hi = Number(draft.scroll_delay_max);
  if (
    [draft.scroll_delay_min, draft.scroll_delay_max].some(
      (v) => v.trim() === "" || isNaN(Number(v)),
    )
  ) {
    errors.scroll = "Enter both delays in seconds.";
  } else {
    if (lo !== saved.scroll_delay_min) patch.scroll_delay_min = lo;
    if (hi !== saved.scroll_delay_max) patch.scroll_delay_max = hi;
  }

  const time = draft.daily_check_time.trim();
  if (!HHMM.test(time)) errors.time = "Enter a time, like 09:00.";
  else if (time !== saved.daily_check_time) patch.daily_check_time = time;

  if (draft.desktop_notifications !== saved.desktop_notifications) {
    patch.desktop_notifications = draft.desktop_notifications;
  }

  const cap = Number(draft.max_auto_analyses_per_day);
  if (
    draft.max_auto_analyses_per_day.trim() === "" ||
    !Number.isInteger(cap) ||
    cap < 0 ||
    cap > 100
  ) {
    errors.autoCap = "Enter a whole number from 0 to 100.";
  } else if (cap !== saved.max_auto_analyses_per_day) patch.max_auto_analyses_per_day = cap;
  return { patch, errors };
}

/** Puts the API's 422 message next to the input it is about. */
function fieldFor(detail: string): Field {
  if (/daily|HH:MM|time/i.test(detail)) return "time";
  if (/auto_analyses|analyses per day/i.test(detail)) return "autoCap";
  if (/scroll|delay/i.test(detail)) return "scroll";
  if (/max_results|limit|1000/i.test(detail)) return "limit";
  return "form";
}

export function SettingsPage() {
  const settings = useSettings();

  return (
    <div className="max-w-[880px] space-y-5">
      <header className="space-y-1">
        <h1 className="text-24 font-semibold tracking-[-0.01em] text-text">Settings</h1>
        <p className="text-13 text-muted">
          Saved in your data folder. Values here take priority over .env.
        </p>
      </header>

      {settings.isPending ? (
        <div className="space-y-3 border border-rule bg-panel p-6" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : settings.isError || !settings.data ? (
        <div className="space-y-1 border border-rule bg-panel p-6">
          <p className="text-13 text-text">Can&apos;t load settings.</p>
          <p className="text-13 text-muted">
            {settings.error instanceof ApiError
              ? settings.error.detail
              : "Check that AdScout is still running."}
          </p>
        </div>
      ) : (
        <SettingsForm saved={settings.data} />
      )}
    </div>
  );
}

function SettingsForm({ saved }: { saved: Settings }) {
  const update = useUpdateSettings();
  const [draft, setDraft] = useState<Draft>(() => draftOf(saved));
  const [errors, setErrors] = useState<Errors>({});
  const ids = {
    limit: useId(),
    min: useId(),
    max: useId(),
    model: useId(),
    auto: useId(),
    limitError: useId(),
    scrollError: useId(),
    time: useId(),
    timeError: useId(),
    notify: useId(),
    autoCap: useId(),
    autoCapError: useId(),
  };

  const dirty = JSON.stringify(draftOf(saved)) !== JSON.stringify(draft);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
  };

  const save = (event: React.FormEvent) => {
    event.preventDefault();
    const { patch: body, errors: local } = patchOf(draft, saved);
    setErrors(local);
    if (Object.keys(local).length > 0) return;
    update.mutate(body, {
      onSuccess: (next) => {
        setDraft(draftOf(next));
        toast.success("Settings saved");
      },
      onError: (error) => {
        const detail = error instanceof ApiError ? error.detail : error.message;
        if (error instanceof ApiError && error.status === 422) {
          setErrors({ [fieldFor(detail)]: detail });
        } else {
          toast.error(detail);
        }
      },
    });
  };

  return (
    <form onSubmit={save} noValidate className="space-y-5">
      <Panel title="Angle analysis">
        <Row
          label="Backend"
          help="Claude Code uses your Claude subscription. The Anthropic API bills your API key. Auto picks the API when a key is set."
        >
          <ToggleGroup
            type="single"
            value={draft.analysis_backend}
            aria-label="Analysis backend"
            onValueChange={(value) => {
              const match = BACKENDS.find((b) => b.value === value);
              if (match) set("analysis_backend", match.value);
            }}
          >
            {BACKENDS.map((b) => (
              <ToggleGroupItem key={b.value} value={b.value}>
                {b.label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <Availability
            settings={saved}
            pending={draft.analysis_backend !== saved.analysis_backend}
          />
        </Row>

        <Row
          label="Model"
          htmlFor={ids.model}
          help="Sonnet balances depth and cost. Opus reads closest; Haiku is fastest and cheapest."
        >
          <Select
            value={draft.analysis_model}
            onValueChange={(value) => {
              const match = MODELS.find((m) => m === value);
              if (match) set("analysis_model", match);
            }}
          >
            <SelectTrigger id={ids.model} className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MODELS.map((model) => (
                <SelectItem key={model} value={model}>
                  {modelLabel(model)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Row>

        <Row
          label="Analyze angles after each search"
          htmlFor={ids.auto}
          help="Runs in the background when a search finishes. Each search then uses your Claude quota."
        >
          <div className="flex h-8 items-center gap-3">
            <Switch
              id={ids.auto}
              checked={draft.auto_analyze}
              onCheckedChange={(checked) => set("auto_analyze", checked)}
            />
            <span className="text-13 text-muted">{draft.auto_analyze ? "On" : "Off"}</span>
          </div>
        </Row>

        <Row
          label="Daily analysis limit"
          htmlFor={ids.autoCap}
          help="Analyses that run without you (after a search or a daily check) stop at this many a day. Ones you start are never limited."
          error={errors.autoCap}
          errorId={ids.autoCapError}
        >
          <div className="flex items-center gap-2">
            <Input
              id={ids.autoCap}
              type="number"
              inputMode="numeric"
              min={0}
              max={100}
              step={1}
              value={draft.max_auto_analyses_per_day}
              onChange={(e) => set("max_auto_analyses_per_day", e.target.value)}
              aria-invalid={errors.autoCap ? true : undefined}
              aria-describedby={errors.autoCap ? ids.autoCapError : undefined}
              className="num w-28"
            />
            <span className="text-13 text-muted">a day</span>
          </div>
        </Row>
      </Panel>

      <Panel title="Watchlist">
        <Row
          label="Daily check time"
          htmlFor={ids.time}
          help={`Watched searches run once a day from this time, one at a time, in ${saved.timezone}. If AdScout is closed then, the check runs when you next open it.`}
          error={errors.time}
          errorId={ids.timeError}
        >
          <Input
            id={ids.time}
            type="time"
            step={60}
            value={draft.daily_check_time}
            onChange={(e) => set("daily_check_time", e.target.value)}
            aria-invalid={errors.time ? true : undefined}
            aria-describedby={errors.time ? ids.timeError : undefined}
            className="num w-32"
          />
        </Row>

        <Row
          label="Desktop notifications"
          htmlFor={ids.notify}
          help="Shows an alert on your desktop when a daily check finds a big change."
        >
          <div className="flex h-8 items-center gap-3">
            {saved.notifications_available ? (
              <Switch
                id={ids.notify}
                checked={draft.desktop_notifications}
                onCheckedChange={(checked) => set("desktop_notifications", checked)}
              />
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  {/* Disabled controls swallow pointer events; the wrapper carries the tooltip. */}
                  <span tabIndex={0} className="inline-flex rounded-sm">
                    <Switch id={ids.notify} checked={false} disabled />
                  </span>
                </TooltipTrigger>
                <TooltipContent>{NOTIFY_INSTALL_COPY}</TooltipContent>
              </Tooltip>
            )}
            <span className="text-13 text-muted">
              {saved.notifications_available
                ? draft.desktop_notifications
                  ? "On"
                  : "Off"
                : "Not installed"}
            </span>
          </div>
        </Row>
      </Panel>

      <Panel title="Search">
        <Row
          label="Search limit"
          htmlFor={ids.limit}
          help="Ads collected per search, up to 1,000. Higher limits take longer to scroll."
          error={errors.limit}
          errorId={ids.limitError}
        >
          <div className="flex items-center gap-2">
            <Input
              id={ids.limit}
              type="number"
              inputMode="numeric"
              min={1}
              max={1000}
              step={1}
              value={draft.max_results}
              onChange={(e) => set("max_results", e.target.value)}
              aria-invalid={errors.limit ? true : undefined}
              aria-describedby={errors.limit ? ids.limitError : undefined}
              className="num w-28"
            />
            <span className="text-13 text-muted">ads</span>
          </div>
        </Row>

        <Row
          label="Scroll delay"
          help="AdScout waits a random time in this range between scrolls. Longer delays are gentler on Meta."
          error={errors.scroll}
          errorId={ids.scrollError}
        >
          <div className="flex flex-wrap items-end gap-3">
            <DelayInput
              id={ids.min}
              label="Shortest delay"
              value={draft.scroll_delay_min}
              onChange={(v) => set("scroll_delay_min", v)}
              invalid={errors.scroll !== undefined}
              errorId={ids.scrollError}
            />
            <span className="pb-2 text-13 text-muted">to</span>
            <DelayInput
              id={ids.max}
              label="Longest delay"
              value={draft.scroll_delay_max}
              onChange={(v) => set("scroll_delay_max", v)}
              invalid={errors.scroll !== undefined}
              errorId={ids.scrollError}
            />
            <span className="pb-2 text-13 text-muted">seconds</span>
          </div>
        </Row>
      </Panel>

      <Panel title="Display">
        <Row
          label="Timezone"
          help="Dates in reports use this zone. Set TIMEZONE in .env to change it."
        >
          <p className="flex h-8 items-center text-13 text-text">{saved.timezone}</p>
        </Row>
      </Panel>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Button type="submit" disabled={!dirty || update.isPending}>
          Save settings
        </Button>
        {dirty ? (
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setDraft(draftOf(saved));
              setErrors({});
            }}
          >
            Discard changes
          </Button>
        ) : null}
        {errors.form ? (
          <p role="alert" className="text-13 text-fail">
            {errors.form}
          </p>
        ) : dirty ? (
          <p className="text-13 text-muted">Unsaved changes</p>
        ) : null}
      </div>
    </form>
  );
}

function Availability({ settings, pending }: { settings: Settings; pending: boolean }) {
  const { analysis } = settings;
  const label = backendLabel(analysis.backend);
  return (
    <div className="mt-2.5 space-y-1">
      <p className="flex items-start gap-2 text-13 text-pretty text-text">
        <span
          aria-hidden="true"
          className={cn(
            "mt-[5px] size-2 shrink-0 rounded-full",
            analysis.available ? "bg-pass" : "bg-warn",
          )}
        />
        {analysis.available
          ? settings.analysis_backend === "auto"
            ? `Ready. Auto is using ${label}.`
            : `Ready. Using ${label}.`
          : (analysis.reason ?? UNAVAILABLE_COPY)}
      </p>
      {pending ? <p className="pl-4 text-12 text-muted">Save to check the new backend.</p> : null}
    </div>
  );
}

function DelayInput({
  id,
  label,
  value,
  onChange,
  invalid,
  errorId,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  invalid: boolean;
  errorId: string;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-12 text-muted">
        {label}
      </label>
      <Input
        id={id}
        type="number"
        inputMode="decimal"
        min={0.5}
        max={30}
        step={0.5}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? errorId : undefined}
        className="num w-24"
      />
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="border border-rule bg-panel">
      <h2 id={id} className="border-b border-rule px-5 py-3 text-15 font-medium text-text md:px-6">
        {title}
      </h2>
      <div className="divide-y divide-rule">{children}</div>
    </section>
  );
}

function Row({
  label,
  help,
  htmlFor,
  error,
  errorId,
  children,
}: {
  label: string;
  help: string;
  htmlFor?: string;
  error?: string | undefined;
  errorId?: string;
  children: ReactNode;
}) {
  const labelClass = "block text-13 font-medium text-text";
  return (
    <div className="grid gap-3 px-5 py-4 md:grid-cols-[260px_minmax(0,1fr)] md:gap-8 md:px-6">
      <div className="space-y-1">
        {htmlFor ? (
          <label htmlFor={htmlFor} className={labelClass}>
            {label}
          </label>
        ) : (
          <p className={labelClass}>{label}</p>
        )}
        <p className="text-12 text-pretty text-muted">{help}</p>
      </div>
      <div className="min-w-0">
        {children}
        {error ? (
          <p id={errorId} role="alert" className="mt-2 text-13 text-fail">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}
