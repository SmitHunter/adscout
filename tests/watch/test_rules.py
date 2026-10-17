"""Trend series and the alert rule for watch items (pure)."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from adscout.watch.rules import (
    BLOCKED_MESSAGE,
    AlertDraft,
    SnapshotSummary,
    alert_for,
    trend,
)

T0 = datetime(2026, 10, 1, 23, 30, tzinfo=UTC)


def _s(
    sid: int,
    active: int = 10,
    *,
    keys: set[str] | None = None,
    status: str = "ok",
    day: int = 0,
    advertisers: int = 3,
    winner: int = 50,
    market: int = 40,
) -> SnapshotSummary:
    return SnapshotSummary(
        id=sid,
        started_at=T0 + timedelta(days=day),
        status=status,
        active_relevant=active,
        creative_keys=keys if keys is not None else set(),
        advertisers=advertisers,
        winner=winner,
        market=market,
        capped=status == "capped",
    )


def test_trend_new_creatives_counts_unseen_keys() -> None:
    points = trend(
        [
            _s(3, keys={"b", "c", "d"}, day=2),
            _s(1, keys={"a", "b"}, day=0),
            _s(2, keys={"x"}, day=1, status="blocked"),  # not ok/capped: skipped
            _s(4, keys={"a", "e"}, day=3, status="capped", active=99),
        ],
        ZoneInfo("UTC"),
    )
    assert [p.snapshot_id for p in points] == [1, 3, 4]
    assert [p.new_creatives for p in points] == [2, 2, 1]
    assert [p.capped for p in points] == [False, False, True]
    assert points[2].active_ads == 99


def test_trend_carries_scores_and_local_date() -> None:
    [p] = trend([_s(1, advertisers=7, winner=81, market=62)], ZoneInfo("Australia/Melbourne"))
    # 2026-10-01 23:30 UTC is 2 October in Melbourne.
    assert p.date == date(2026, 10, 2)
    assert (p.advertisers, p.winner_score, p.market_score) == (7, 81, 62)


def test_alert_up_30() -> None:
    draft = alert_for(_s(1, 10), _s(2, 13), "posture corrector")
    assert draft == AlertDraft(
        kind="change",
        change_pct=30.0,
        message="posture corrector: active ads up 30% (10 → 13)",
    )


def test_alert_down_30() -> None:
    draft = alert_for(_s(1, 20), _s(2, 14), "q")
    assert draft is not None
    assert draft.change_pct == -30.0
    assert draft.message == "q: active ads down 30% (20 → 14)"


def test_no_alert_below_30() -> None:
    assert alert_for(_s(1, 10), _s(2, 12), "q") is None
    assert alert_for(_s(1, 10), _s(2, 8), "q") is None


def test_no_alert_prev_below_5() -> None:
    assert alert_for(_s(1, 4), _s(2, 40), "q") is None
    assert alert_for(_s(1, 5), _s(2, 40), "q") is not None


def test_no_alert_without_previous() -> None:
    assert alert_for(None, _s(1, 40), "q") is None


@pytest.mark.parametrize(
    ("prev_capped", "now_capped", "prev", "now", "expected"),
    [
        # Neither capped: both directions alert.
        (False, False, 10, 20, "q: active ads up 100% (10 → 20)"),
        (False, False, 20, 10, "q: active ads down 50% (20 → 10)"),
        # Both capped: skip.
        (True, True, 10, 20, None),
        (True, True, 20, 10, None),
        # Only the new one capped: increases only, worded as a floor.
        (False, True, 10, 20, "q: active ads up ≥ +100% (10 → 20)"),
        (False, True, 20, 10, None),
        # Only the previous one capped: decreases only.
        (True, False, 10, 20, None),
        (True, False, 20, 10, "q: active ads down 50% (20 → 10)"),
    ],
)
def test_capped_floor_rules(
    prev_capped: bool, now_capped: bool, prev: int, now: int, expected: str | None
) -> None:
    a = _s(1, prev, status="capped" if prev_capped else "ok")
    b = _s(2, now, status="capped" if now_capped else "ok")
    draft = alert_for(a, b, "q")
    assert (draft.message if draft else None) == expected


def test_message_copy() -> None:
    draft = alert_for(_s(1, 6), _s(2, 8), "Knee Brace")
    assert draft is not None
    assert draft.message == "Knee Brace: active ads up 33% (6 → 8)"
    floor = alert_for(_s(1, 6), _s(2, 8, status="capped"), "Knee Brace")
    assert floor is not None and floor.message == "Knee Brace: active ads up ≥ +33% (6 → 8)"


def test_blocked_run_raises_block_alert() -> None:
    draft = alert_for(_s(1, 10), _s(2, 0, status="blocked"), "q")
    assert draft == AlertDraft(kind="blocked", change_pct=None, message=BLOCKED_MESSAGE)
    assert BLOCKED_MESSAGE == "Meta blocked the daily check. Open the app to sign in."


def test_no_alert_for_failed_runs() -> None:
    assert alert_for(_s(1, 10), _s(2, 0, status="error"), "q") is None
    assert alert_for(_s(1, 10, status="error"), _s(2, 40), "q") is None


def test_alert_pct_rounds_half_up() -> None:
    draft = alert_for(_s(1, 8), _s(2, 13), "q")
    assert draft is not None
    assert draft.message == "q: active ads up 63% (8 → 13)"
    assert draft.change_pct == 62.5


def test_trend_new_creatives_stable_when_groups_split() -> None:
    # Snapshot 1 grouped raw keys a+b into one creative; snapshot 2 only has b.
    first = _s(1, day=0).model_copy(
        update={"creative_keys": {"a", "b"}, "creatives": [frozenset({"a", "b"})]}
    )
    second = _s(2, day=1).model_copy(
        update={"creative_keys": {"b"}, "creatives": [frozenset({"b"})]}
    )
    third = _s(3, day=2).model_copy(
        update={"creative_keys": {"b", "c"}, "creatives": [frozenset({"b", "c"})]}
    )
    points = trend([first, second, third], ZoneInfo("UTC"))
    assert [p.new_creatives for p in points] == [1, 0, 0]
