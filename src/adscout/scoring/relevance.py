import unicodedata
from collections.abc import Collection
from typing import Literal

from pydantic import BaseModel

from adscout.models import Ad, SearchKind


class Override(BaseModel):
    scope: Literal["ad", "advertiser"]
    target_id: str
    mode: Literal["exclude", "include"]


RelevanceReason = Literal[
    "keyword", "no_keyword", "ad_override", "advertiser_override", "template", "claude_off_topic"
]

# Dropped from the keyword's tokens when matching only; override keys keep every token.
STOP_WORDS = frozenset(
    {"a", "an", "and", "the", "for", "with", "of", "to", "in", "on", "by", "your", "my"}
)


class RelevanceDecision(BaseModel):
    ad_archive_id: str
    relevant: bool
    reason: RelevanceReason


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith(("sses", "xes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokens(text: str) -> list[str]:
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).lower()
    spaced = "".join(c if c.isalnum() else " " for c in folded)
    return [_stem(t) for t in spaced.split()]


def normalise_keyword(keyword: str) -> str:
    return " ".join(tokens(keyword))


def require_keyword(keyword: str) -> str:
    norm = normalise_keyword(keyword)
    if not norm:
        raise ValueError("keyword has no searchable words")
    return norm


PAGE_KEY_PREFIX = "page:"


def page_override_key(page_id: str) -> str:
    """The override key of an advertiser (page-view) search: never the display name."""
    return f"{PAGE_KEY_PREFIX}{page_id}"


def override_key_norm(key: str) -> str:
    """The stored form of an override key: a ``page:<digits>`` key as is, else the
    normalised keyword (raises ``ValueError`` when it has no searchable words)."""
    key = key.strip()
    page = key.removeprefix(PAGE_KEY_PREFIX)
    if page != key:
        if not (page.isascii() and page.isdigit()):
            raise ValueError("page override key must be page:<numeric page id>")
        return key
    return require_keyword(key)


def is_keyword_match(ad: Ad, keyword: str) -> bool:
    all_wanted = tokens(require_keyword(keyword))
    wanted = [t for t in all_wanted if t not in STOP_WORDS] or all_wanted
    text = " ".join(p for p in (ad.body, ad.title, ad.link_caption, ad.link_url) if p)
    return set(wanted) <= set(tokens(text))


def decide_relevance(
    ads: list[Ad],
    keyword: str,
    overrides: list[Override],
    off_topic: Collection[str] = frozenset(),
    kind: SearchKind = "keyword",
) -> dict[str, RelevanceDecision]:
    """Precedence: ad override > advertiser override > ``off_topic`` > the kind's rule.

    ``off_topic`` holds the ad ids Claude marked as not selling the product (every ad of each
    flagged creative); the user's overrides always win over it.

    The keyword kind's rule is the keyword/template match. For an advertiser snapshot
    (``keyword`` is the advertiser's name) every remaining ad is relevant, reason "keyword".
    """
    if kind == "keyword":
        require_keyword(keyword)
    by_ad = {o.target_id: o.mode for o in overrides if o.scope == "ad"}
    by_adv = {o.target_id: o.mode for o in overrides if o.scope == "advertiser"}
    out: dict[str, RelevanceDecision] = {}
    for ad in ads:
        key = ad.advertiser_key
        if (mode := by_ad.get(ad.ad_archive_id)) is not None:
            relevant, reason = mode == "include", "ad_override"
        elif key is not None and (mode := by_adv.get(key)) is not None:
            relevant, reason = mode == "include", "advertiser_override"
        elif ad.ad_archive_id in off_topic:
            relevant, reason = False, "claude_off_topic"
        elif kind == "advertiser" or is_keyword_match(ad, keyword):
            relevant, reason = True, "keyword"
        elif "{{" in f"{ad.body} {ad.title or ''}":
            relevant, reason = False, "template"
        else:
            relevant, reason = False, "no_keyword"
        out[ad.ad_archive_id] = RelevanceDecision.model_validate(
            {"ad_archive_id": ad.ad_archive_id, "relevant": relevant, "reason": reason}
        )
    return out
