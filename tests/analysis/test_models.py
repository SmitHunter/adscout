import json

import pytest
from pydantic import ValidationError

from adscout.analysis.models import AngleAnalysis, clean


def _raw(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "angles": [
            {"name": "Relief", "description": "d", "emotion": "pain_relief", "ad_ids": ["1", "2"]}
        ],
        "hooks": [{"text": "Tired of pain?", "ad_ids": ["1"]}],
        "offers": [{"kind": "discount", "text": "20% off", "ad_ids": ["2"]}],
        "not_product_ad_ids": ["3"],
        "seasonality": "evergreen",
        "seasonality_note": "year round",
        "verdict": "One. Two. Three.",
    }
    base.update(kw)
    return base


def test_schema_rejects_extra_fields_and_bad_enum() -> None:
    AngleAnalysis.model_validate(_raw())
    with pytest.raises(ValidationError):
        AngleAnalysis.model_validate(_raw(surprise=1))
    bad = _raw(angles=[{"name": "x", "description": "d", "emotion": "joy", "ad_ids": []}])
    with pytest.raises(ValidationError):
        AngleAnalysis.model_validate(bad)
    with pytest.raises(ValidationError):
        AngleAnalysis.model_validate(_raw(seasonality="sometimes"))


def test_schema_has_no_length_constraints() -> None:
    text = json.dumps(AngleAnalysis.model_json_schema())
    assert "maxLength" not in text
    assert "maxItems" not in text
    assert "minItems" not in text


def test_clean_drops_unknown_ids_and_empty_items() -> None:
    raw = _raw(
        angles=[
            {"name": "A", "description": "d", "emotion": "gift", "ad_ids": ["1", "zz"]},
            {"name": "B", "description": "d", "emotion": "value", "ad_ids": ["zz"]},
        ],
        hooks=[{"text": "h", "ad_ids": ["zz"]}, {"text": "h2", "ad_ids": ["2"]}],
        offers=[{"kind": "bundle", "text": "o", "ad_ids": []}],
        not_product_ad_ids=["3", "nope"],
    )
    out = clean(AngleAnalysis.model_validate(raw), {"1", "2", "3"})
    assert [a.name for a in out.angles] == ["A"]
    assert out.angles[0].ad_ids == ["1"]
    assert [h.text for h in out.hooks] == ["h2"]
    assert out.offers == []
    assert out.not_product_ad_ids == ["3"]


def test_clean_drops_blank_text_and_dedupes_ids() -> None:
    raw = _raw(
        angles=[
            {"name": " ", "description": "d", "emotion": "gift", "ad_ids": ["1"]},
            {"name": "A", "description": "d", "emotion": "gift", "ad_ids": ["1", "1", "2"]},
        ],
        not_product_ad_ids=["3", "3"],
    )
    out = clean(AngleAnalysis.model_validate(raw), {"1", "2", "3"})
    assert len(out.angles) == 1
    assert out.angles[0].ad_ids == ["1", "2"]
    assert out.not_product_ad_ids == ["3"]


def test_clean_truncates_strings_and_lists_to_documented_limits() -> None:
    angle = {"name": "n" * 100, "description": "d" * 400, "emotion": "other", "ad_ids": ["0"]}
    raw = _raw(
        angles=[angle for _ in range(14)],
        hooks=[{"text": "h" * 300, "ad_ids": ["0"]} for _ in range(12)],
        offers=[{"kind": "other", "text": "o" * 300, "ad_ids": ["0"]} for _ in range(12)],
        seasonality_note="s" * 400,
        verdict="v" * 900,
    )
    out = clean(AngleAnalysis.model_validate(raw), {"0"})
    assert len(out.angles) == 10
    assert len(out.hooks) == 8
    assert len(out.offers) == 8
    assert len(out.angles[0].name) == 40
    assert len(out.angles[0].description) == 160
    assert len(out.hooks[0].text) == 120
    assert len(out.offers[0].text) == 80
    assert len(out.seasonality_note) == 160
    assert len(out.verdict) == 600


def test_clean_cuts_an_overlong_verdict_at_a_sentence_end() -> None:
    first = "Four angles are in play. " + "Long ads concentrate on relief. " * 12
    verdict = first + "The main risk is saturation and health claims that " + "x" * 300
    out = clean(AngleAnalysis.model_validate(_raw(verdict=verdict)), {"1", "2", "3"})
    assert len(out.verdict) <= 600
    assert out.verdict.endswith("relief.")


def test_clean_ends_overlong_text_at_a_word_with_an_ellipsis() -> None:
    angle = {
        "name": "Instant fix with a waist snatch and scoliosis relief",
        "description": "d",
        "emotion": "pain_relief",
        "ad_ids": ["1"],
    }
    out = clean(AngleAnalysis.model_validate(_raw(angles=[angle])), {"1", "2", "3"})
    assert out.angles[0].name == "Instant fix with a waist snatch and…"
    assert len(out.angles[0].name) <= 40


def test_clean_removes_not_product_ids_from_angles_hooks_and_offers() -> None:
    raw = _raw(
        angles=[
            {"name": "Relief", "description": "d", "emotion": "pain_relief", "ad_ids": ["1", "3"]},
            {"name": "Advertorial", "description": "d", "emotion": "other", "ad_ids": ["3"]},
        ],
        hooks=[{"text": "Neck hump?", "ad_ids": ["3"]}],
        offers=[{"kind": "discount", "text": "20% off", "ad_ids": ["2", "3"]}],
        not_product_ad_ids=["3"],
    )
    out = clean(AngleAnalysis.model_validate(raw), {"1", "2", "3"})
    assert [(a.name, a.ad_ids) for a in out.angles] == [("Relief", ["1"])]
    assert out.hooks == []
    assert out.offers[0].ad_ids == ["2"]
    assert out.not_product_ad_ids == ["3"]
