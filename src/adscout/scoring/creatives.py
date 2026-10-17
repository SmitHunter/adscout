"""Group ads into distinct creatives (pure, no I/O)."""

import hashlib
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel

from adscout.models import Ad


class Creative(BaseModel):
    key: str
    ads: list[Ad]
    first_start: datetime
    ad_count: int


def creative_key(ad: Ad) -> str:
    """Collation id when present, else a hash of normalised body and media path.

    An ad with neither text nor media is its own creative (nothing to match it on).
    """
    if ad.collation_id:
        return f"c:{ad.collation_id}"
    body = " ".join(ad.body.lower().split())
    path = urlsplit(ad.media[0].url).path if ad.media else ""
    if not body and not path:
        return f"a:{ad.ad_archive_id}"
    digest = hashlib.sha1(f"{body}|{path}".encode(), usedforsecurity=False).hexdigest()
    return f"t:{digest[:16]}"


PHASH_MAX_DISTANCE = 6


def _representative_phash(creative: Creative) -> str | None:
    """Phash of the first ad's first image or poster that has one."""
    for item in creative.ads[0].media:
        if item.phash:
            return item.phash
    return None


def _parse_phash(value: str | None) -> tuple[int, int] | None:
    """(hex length, integer value) of a hex phash; None when absent or not hex."""
    if not value:
        return None
    try:
        return len(value), int(value, 16)
    except ValueError:
        return None


def _merge_by_phash(creatives: list[Creative]) -> list[Creative]:
    """Merge creatives whose representative phashes are within PHASH_MAX_DISTANCE bits.

    Hamming distance is the popcount of the XOR (same as imagehash's ``a - b``); hashes
    of different lengths never match. Each representative is parsed once.
    """
    kept: list[Creative] = []
    kept_reps: list[tuple[int, int, int]] = []  # (hex length, value, index into kept)
    for c in creatives:
        rep = _parse_phash(_representative_phash(c))
        target: int | None = None
        if rep is not None:
            length, value = rep
            for k_len, k_value, i in kept_reps:
                if k_len == length and (value ^ k_value).bit_count() <= PHASH_MAX_DISTANCE:
                    target = i
                    break
        if target is None:
            if rep is not None:
                kept_reps.append((rep[0], rep[1], len(kept)))
            kept.append(c)
            continue
        k = kept[target]
        ads = k.ads + c.ads
        kept[target] = Creative(
            key=min(k.key, c.key),
            ads=ads,
            first_start=min(k.first_start, c.first_start),
            ad_count=sum(a.collation_count for a in ads),
        )
    return kept


def group_creatives(ads: list[Ad]) -> list[Creative]:
    groups: dict[str, dict[str, Ad]] = {}
    for ad in ads:
        groups.setdefault(creative_key(ad), {}).setdefault(ad.ad_archive_id, ad)
    out = [
        Creative(
            key=key,
            ads=list(members.values()),
            first_start=min(a.start_date for a in members.values()),
            ad_count=sum(a.collation_count for a in members.values()),
        )
        for key, members in groups.items()
    ]
    out.sort(key=lambda c: (c.first_start, c.key))
    out = _merge_by_phash(out)
    out.sort(key=lambda c: (c.first_start, c.key))
    return out
