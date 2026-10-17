"""Angle-analysis result schema and post-validation clean-up (pure, no I/O).

The schema deliberately carries no length or count limits: structured-output backends may
reject them, and a slightly verbose answer must not fail. ``clean`` enforces the documented
limits by truncation instead.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

Emotion = Literal[
    "pain_relief",
    "confidence",
    "appearance",
    "convenience",
    "health",
    "gift",
    "fear_of_missing_out",
    "social_proof",
    "value",
    "other",
]
OfferKind = Literal[
    "discount", "bundle", "free_shipping", "guarantee", "free_gift", "urgency", "other"
]
Seasonality = Literal["evergreen", "seasonal", "unclear"]

MAX_ANGLES = 10
MAX_HOOKS = 8
MAX_OFFERS = 8
NAME_MAX = 40
ANGLE_DESCRIPTION_MAX = 160
HOOK_MAX = 120
OFFER_MAX = 80
SEASONALITY_NOTE_MAX = 160
VERDICT_MAX = 600


class Angle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    emotion: Emotion
    ad_ids: list[str]


class Hook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    ad_ids: list[str]


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: OfferKind
    text: str
    ad_ids: list[str]


class AngleAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    angles: list[Angle]
    hooks: list[Hook]
    offers: list[Offer]
    not_product_ad_ids: list[str]
    seasonality: Seasonality
    seasonality_note: str
    verdict: str


def _ids(ids: list[str], sent: set[str]) -> list[str]:
    """Known ids only, first occurrence kept, order preserved."""
    seen: dict[str, None] = {}
    for i in ids:
        if i in sent:
            seen.setdefault(i, None)
    return list(seen)


_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
# Characters stripped (as a set) before the ellipsis: spaces, commas, colons, dashes.
_DANGLING = " ,;:-–—"  # noqa: RUF001


def _cut(text: str, limit: int, *, sentences: bool = False) -> str:
    """``text`` (stripped) within ``limit`` characters; unchanged if it already fits.

    An over-long text ends with an ellipsis, cut at the last space when that keeps at least
    half the limit, else mid-word at ``limit - 1``; trailing spaces and punctuation from
    ``_DANGLING`` are dropped before the ellipsis. With ``sentences``, it first tries to keep
    only whole sentences (no ellipsis) when they fill at least half the limit.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    if sentences:
        # endpos one past the limit so the lookahead sees the real next character.
        ends = [e for m in _SENTENCE_END.finditer(text, 0, limit + 1) if (e := m.end()) <= limit]
        if ends and ends[-1] >= limit // 2:
            return text[: ends[-1]]
    head = text[: limit - 1]
    space = head.rfind(" ")
    if space >= limit // 2:
        head = head[:space]
    return head.rstrip(_DANGLING) + "…"


def clean(result: AngleAnalysis, sent_ids: set[str]) -> AngleAnalysis:
    """Drop unknown ids and empty items, then enforce the documented limits by truncation.

    An ad Claude flags as not selling the product can't also be evidence for an angle, hook or
    offer, so flagged ids are removed from those (and an angle left with none is dropped).
    """
    not_product = _ids(result.not_product_ad_ids, sent_ids)
    selling = sent_ids - set(not_product)
    angles: list[Angle] = []
    for a in result.angles:
        ids = _ids(a.ad_ids, selling)
        name = _cut(a.name, NAME_MAX)
        if ids and name:
            angles.append(
                Angle(
                    name=name,
                    description=_cut(a.description, ANGLE_DESCRIPTION_MAX),
                    emotion=a.emotion,
                    ad_ids=ids,
                )
            )
    hooks: list[Hook] = []
    for h in result.hooks:
        ids = _ids(h.ad_ids, selling)
        text = _cut(h.text, HOOK_MAX)
        if ids and text:
            hooks.append(Hook(text=text, ad_ids=ids))
    offers: list[Offer] = []
    for o in result.offers:
        ids = _ids(o.ad_ids, selling)
        text = _cut(o.text, OFFER_MAX)
        if ids and text:
            offers.append(Offer(kind=o.kind, text=text, ad_ids=ids))
    return AngleAnalysis(
        angles=angles[:MAX_ANGLES],
        hooks=hooks[:MAX_HOOKS],
        offers=offers[:MAX_OFFERS],
        not_product_ad_ids=not_product,
        seasonality=result.seasonality,
        seasonality_note=_cut(result.seasonality_note, SEASONALITY_NOTE_MAX),
        verdict=_cut(result.verdict, VERDICT_MAX, sentences=True),
    )
