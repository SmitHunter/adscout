"""Winner Score thresholds and weights. The single source of truth."""

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

FactorKey = Literal["longevity", "continuity", "volume", "angles"]


class Threshold(BaseModel):
    """`pass_` is "pass" in JSON (dump with by_alias=True); either name is accepted on input."""

    model_config = ConfigDict(populate_by_name=True)

    fail: float
    pass_: float = Field(serialization_alias="pass", validation_alias=AliasChoices("pass", "pass_"))
    full: float | None = None


LONGEVITY_DAYS = Threshold(fail=30, pass_=90, full=180)
LONGEVITY_SHARE_90D = Threshold(fail=0.05, pass_=0.25, full=0.50)
CONTINUITY_MONTHS = Threshold(fail=3, pass_=5, full=None)
VOLUME_CREATIVES = Threshold(fail=3, pass_=10, full=20)
ANGLE_CLUSTERS = Threshold(fail=2, pass_=3, full=5)

WEIGHTS: dict[FactorKey, int] = {
    "longevity": 30,
    "volume": 25,
    "angles": 25,
    "continuity": 20,
}

# (max advertiser count inclusive, label); anything above the last is "crowded".
SATURATION_BANDS: list[tuple[int, Literal["early", "healthy", "crowded"]]] = [
    (2, "early"),
    (15, "healthy"),
]
