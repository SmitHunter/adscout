import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** One-click markets. EU goes through the official API, so it needs a token. */
const QUICK_COUNTRIES = ["US", "AU", "GB", "CA"] as const;
const EU = "EU";

/** Other common ad markets, by ISO 3166-1 alpha-2 code. */
const OTHER_COUNTRIES: readonly (readonly [code: string, name: string])[] = [
  ["AE", "United Arab Emirates"],
  ["AR", "Argentina"],
  ["AT", "Austria"],
  ["BE", "Belgium"],
  ["BR", "Brazil"],
  ["CH", "Switzerland"],
  ["CL", "Chile"],
  ["CO", "Colombia"],
  ["CZ", "Czechia"],
  ["DE", "Germany"],
  ["DK", "Denmark"],
  ["ES", "Spain"],
  ["FI", "Finland"],
  ["FR", "France"],
  ["IE", "Ireland"],
  ["IL", "Israel"],
  ["IN", "India"],
  ["IT", "Italy"],
  ["JP", "Japan"],
  ["KR", "South Korea"],
  ["MX", "Mexico"],
  ["MY", "Malaysia"],
  ["NL", "Netherlands"],
  ["NO", "Norway"],
  ["NZ", "New Zealand"],
  ["PH", "Philippines"],
  ["PL", "Poland"],
  ["PT", "Portugal"],
  ["SA", "Saudi Arabia"],
  ["SE", "Sweden"],
  ["SG", "Singapore"],
  ["ZA", "South Africa"],
];

const isQuick = (code: string) =>
  (QUICK_COUNTRIES as readonly string[]).includes(code) || code === EU;

interface CountryToggleProps {
  value: string;
  onChange: (country: string) => void;
  /** EU searches use the official API, which needs a Meta access token. */
  euAvailable: boolean;
}

export function CountryToggle({ value, onChange, euAvailable }: CountryToggleProps) {
  const other = isQuick(value) ? "" : value;

  return (
    <div className="flex items-stretch">
      <ToggleGroup
        type="single"
        value={isQuick(value) ? value : ""}
        aria-label="Country"
        className="rounded-r-none"
        onValueChange={(next) => {
          // "" means the active item was clicked again: keep the country.
          if (next === "" || (next === EU && !euAvailable)) return;
          onChange(next);
        }}
      >
        {QUICK_COUNTRIES.map((code) => (
          <ToggleGroupItem key={code} value={code} className="w-10 px-0">
            {code}
          </ToggleGroupItem>
        ))}
        {euAvailable ? (
          <ToggleGroupItem value={EU} className="w-10 px-0">
            {EU}
          </ToggleGroupItem>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <ToggleGroupItem
                value={EU}
                aria-disabled="true"
                className="w-10 cursor-not-allowed px-0 opacity-50 hover:text-muted"
              >
                {EU}
              </ToggleGroupItem>
            </TooltipTrigger>
            <TooltipContent>EU needs a Meta API token</TooltipContent>
          </Tooltip>
        )}
      </ToggleGroup>
      <Select value={other} onValueChange={onChange}>
        <SelectTrigger
          aria-label="Other country"
          className={cn(
            "h-auto w-[74px] rounded-l-none border-l-0 px-2",
            other !== "" && "bg-rule text-text",
          )}
        >
          <SelectValue placeholder="More">{other || undefined}</SelectValue>
        </SelectTrigger>
        <SelectContent className="max-h-72 min-w-56">
          {OTHER_COUNTRIES.map(([code, name]) => (
            <SelectItem key={code} value={code}>
              <span className="inline-block w-8 font-medium text-text">{code}</span>
              <span className="text-muted">{name}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
