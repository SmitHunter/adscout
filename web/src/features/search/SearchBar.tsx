import type { RefObject } from "react";
import { SearchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { DemoLock } from "@/features/demo/DemoLock";

import { CountryToggle } from "./CountryToggle";
import type { SearchForm } from "./form";

const MEDIA_OPTIONS: readonly (readonly [SearchForm["media_type"], string])[] = [
  ["all", "All media"],
  ["image", "Images"],
  ["video", "Videos"],
  ["meme", "Memes"],
];

interface SearchBarProps {
  value: SearchForm;
  onChange: (next: SearchForm) => void;
  onSubmit: (form: SearchForm) => void;
  /** A search is starting or running: submitting would only get a 409. */
  busy: boolean;
  euAvailable: boolean;
  /** `adscout demo`: searching is off. */
  demo?: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
}

/** The command line: keyword, market, status, media and go, on one row. */
export function SearchBar({
  value,
  onChange,
  onSubmit,
  busy,
  euAvailable,
  demo = false,
  inputRef,
}: SearchBarProps) {
  const set = <K extends keyof SearchForm>(key: K, next: SearchForm[K]) =>
    onChange({ ...value, [key]: next });

  return (
    <form
      role="search"
      aria-label="Search the Ad Library"
      className="flex flex-wrap items-stretch gap-2 border border-rule bg-panel p-2 lg:flex-nowrap"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(value);
      }}
    >
      <label className="relative flex min-w-[60%] flex-1 items-center max-lg:-order-1 lg:min-w-0">
        <span className="sr-only">Product keyword</span>
        <SearchIcon
          aria-hidden="true"
          className="pointer-events-none absolute left-2.5 size-4 text-muted"
        />
        <input
          ref={inputRef}
          autoFocus
          type="text"
          name="keyword"
          autoComplete="off"
          spellCheck={false}
          maxLength={100}
          placeholder="Product keyword, like posture corrector"
          value={value.keyword}
          onChange={(event) => set("keyword", event.target.value)}
          className="h-8 w-full min-w-0 rounded-sm border border-transparent bg-transparent pr-2.5 pl-8 text-15 text-text placeholder:text-muted focus-visible:outline-offset-0"
        />
      </label>

      <CountryToggle
        value={value.country}
        onChange={(country) => set("country", country)}
        euAvailable={euAvailable}
      />

      <ToggleGroup
        type="single"
        value={value.active_status}
        aria-label="Ad status"
        onValueChange={(next) => {
          if (next === "active" || next === "all") set("active_status", next);
        }}
      >
        <ToggleGroupItem value="active">Active</ToggleGroupItem>
        <ToggleGroupItem value="all">All ads</ToggleGroupItem>
      </ToggleGroup>

      <Select
        value={value.media_type}
        onValueChange={(next) => {
          const option = MEDIA_OPTIONS.find(([key]) => key === next);
          if (option) set("media_type", option[0]);
        }}
      >
        <SelectTrigger aria-label="Media type" className="h-auto w-28">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {MEDIA_OPTIONS.map(([key, label]) => (
            <SelectItem key={key} value={key}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <DemoLock locked={demo}>
        <Button
          type="submit"
          disabled={busy || demo}
          className="h-auto min-h-8 px-5 max-lg:-order-1"
        >
          Search
        </Button>
      </DemoLock>
    </form>
  );
}
