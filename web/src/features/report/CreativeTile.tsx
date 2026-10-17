import { useState } from "react";
import { ImageOff, Play } from "lucide-react";

import { formatDate, formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  advertiserName,
  copyPreview,
  excludeCalls,
  groupReasons,
  imageSources,
  includeCalls,
  localVideo,
  REASON_COPY,
  toggleSubject,
  type CreativeGroup,
} from "./gallery";
import { RelevanceToggle } from "./RelevanceToggle";

export type TileMode = "relevant" | "excluded";

interface CreativeTileProps {
  group: CreativeGroup;
  mode: TileMode;
  pending: boolean;
  timezone: string;
  onToggle: (group: CreativeGroup) => void;
}

/** One creative: its media, then a dense data strip, then the relevance toggle. */
export function CreativeTile({ group, mode, pending, timezone, onToggle }: CreativeTileProps) {
  const { lead } = group;
  const name = advertiserName(lead);
  const reasons = mode === "excluded" ? groupReasons(group) : [];
  const subject = toggleSubject(
    group.ads,
    mode === "excluded" ? includeCalls(group.ads) : excludeCalls(group.ads),
  );

  return (
    <li
      data-testid="creative-tile"
      className={cn("flex min-w-0 flex-col border border-rule bg-panel", pending && "opacity-60")}
    >
      <TileMedia group={group} />

      <div className="flex flex-1 flex-col gap-2 px-3 pt-2.5 pb-2 font-dense">
        <div className="flex items-baseline justify-between gap-3">
          <p className="flex items-baseline gap-1.5">
            <span data-testid="tile-days" className="num text-18 font-medium text-text">
              {formatInt(lead.days_running)}
            </span>
            <span className="text-12 text-muted">{lead.days_running === 1 ? "day" : "days"}</span>
          </p>
          <time dateTime={lead.start_date} className="num text-12 text-muted">
            {formatDate(lead.start_date, timezone)}
          </time>
        </div>

        <div className="flex min-w-0 items-baseline justify-between gap-3">
          <p className="min-w-0 truncate text-13 font-medium text-text" title={name}>
            {name}
          </p>
          {group.adCount > 1 ? (
            <p data-testid="ad-count" className="shrink-0 text-12 whitespace-nowrap text-muted">
              <span className="num">{formatInt(group.adCount)}</span> ads use this
            </p>
          ) : null}
        </div>

        <AdCopy text={lead.body || lead.title || ""} />

        <div className="mt-auto flex min-h-7 items-center justify-between gap-2 border-t border-rule pt-2">
          {reasons.length > 0 ? (
            <ul className="flex min-w-0 flex-wrap gap-1" aria-label="Why it's excluded">
              {reasons.map((reason) => {
                const userExcluded = reason === "ad_override" || reason === "advertiser_override";
                return (
                  <li
                    key={reason}
                    className={cn(
                      "truncate rounded-sm border px-1.5 py-px text-12",
                      // The keyword filter's calls are worth a second look; the user's own are settled.
                      userExcluded ? "border-rule text-muted" : "border-warn/50 text-warn",
                    )}
                  >
                    {REASON_COPY[reason]}
                  </li>
                );
              })}
            </ul>
          ) : (
            <span className="truncate text-12 text-muted">{lead.landing_domain ?? ""}</span>
          )}
          <RelevanceToggle
            excluded={mode === "excluded"}
            subject={subject}
            pending={pending}
            onToggle={() => onToggle(group)}
            className="-mr-1.5 shrink-0"
          />
        </div>
      </div>
    </li>
  );
}

function TileMedia({ group }: { group: CreativeGroup }) {
  const item = group.lead.media[0];
  const sources = imageSources(item);
  const video = localVideo(item);
  const [failed, setFailed] = useState(0);
  const [playing, setPlaying] = useState(false);
  const src = sources[failed];
  const isVideo = item?.type === "video";

  return (
    <div className="relative aspect-square overflow-hidden border-b border-rule bg-bg">
      {playing && video ? (
        <video
          src={video}
          poster={src}
          controls
          autoPlay
          playsInline
          className="size-full object-contain"
        />
      ) : src ? (
        <img
          key={src}
          src={src}
          alt={`Ad creative from ${advertiserName(group.lead)}`}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailed((n) => n + 1)}
          className="size-full object-cover"
        />
      ) : (
        <div className="flex size-full flex-col items-center justify-center gap-2 text-muted">
          <ImageOff className="size-5" aria-hidden="true" />
          <span className="text-12">Media unavailable</span>
        </div>
      )}

      {isVideo && video && !playing ? (
        <button
          type="button"
          onClick={() => setPlaying(true)}
          aria-label={`Play video ad from ${advertiserName(group.lead)}`}
          className="absolute inset-0 flex cursor-pointer items-center justify-center focus-visible:-outline-offset-2"
        >
          <span className="flex size-11 items-center justify-center rounded-full border border-text/40 bg-bg/75 text-text">
            <Play className="size-4 translate-x-px fill-current" aria-hidden="true" />
          </span>
        </button>
      ) : null}

      <div className="pointer-events-none absolute inset-x-2 top-2 flex items-start justify-between gap-2">
        {group.variants > 0 ? (
          <span data-testid="variants-badge" className="bg-bg/85 px-1.5 py-px text-12 text-text">
            +<span className="num">{group.variants}</span>{" "}
            {group.variants === 1 ? "variant" : "variants"}
          </span>
        ) : (
          <span />
        )}
        {isVideo ? <span className="bg-bg/85 px-1.5 py-px text-12 text-muted">Video</span> : null}
      </div>
    </div>
  );
}

function AdCopy({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const { short, truncated } = copyPreview(text);
  if (!short) return <p className="text-12 text-muted italic">No ad copy</p>;
  const body = open ? text.trim() : short;
  if (!truncated) {
    return <p className="text-12 leading-[17px] break-words text-muted">{body}</p>;
  }
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={() => setOpen((v) => !v)}
      className="cursor-pointer text-left text-12 leading-[17px] break-words whitespace-pre-line text-muted hover:text-text"
    >
      {body}
      <span className="sr-only">{open ? " Show less" : " Show all"}</span>
    </button>
  );
}
