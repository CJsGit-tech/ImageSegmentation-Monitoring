"""Simulator value model, live window logic and the custom range rules (no database needed)."""

import random
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.config import ConfigurationError, load_settings
from app.realtime.history import (
    BUCKET_CHOICES,
    RangeTooLongError,
    add_months,
    bucket_label,
    check_range,
    choose_bucket,
)
from app.realtime.hub import WINDOW_SECONDS, RealtimeHub, Reading, utc_now
from app.realtime.schemas import RangeParams
from app.records.anomaly import CategoryRange, is_out_of_range
from app.simulator.devices import DEVICE_DEFAULTS, DEVICES
from app.simulator.generator import ValueModel

# --- Devices and defaults (PRD-07-R7, R8, R11) ---


def test_four_devices_with_design_names_units_and_ranges():
    assert [(d.category, d.title, d.unit, d.range_min, d.range_max) for d in DEVICES] == [
        ("溫度", "溫度感測器 T-01", "°C", 20, 80),
        ("壓力", "壓力感測器 P-01", "kPa", 100, 500),
        ("馬達轉速", "馬達轉速計 R-01", "rpm", 1000, 3000),
        ("振動", "振動感測器 V-01", "mm/s", 0.5, 8),
    ]
    assert [(d.category, d.range_min, d.range_max, d.unit) for d in DEVICE_DEFAULTS] == [
        (d.category, d.range_min, d.range_max, d.unit) for d in DEVICES
    ]


# --- Value model (PRD-07-R9) ---


def _sample(device, category_range, count, seed=7):
    model = ValueModel(device, random.Random(seed))
    return [model.next_value(category_range) for _ in range(count)]


@pytest.mark.parametrize("device", DEVICES, ids=lambda d: d.category)
def test_about_five_percent_out_of_range_and_normal_values_never_touch_a_bound(device):
    category_range = CategoryRange(min=device.range_min, max=device.range_max)
    values = _sample(device, category_range, 20_000)
    out = [value for value in values if is_out_of_range(value, category_range)]
    assert 0.044 <= len(out) / len(values) <= 0.056
    inside = [value for value in values if not is_out_of_range(value, category_range)]
    assert all(device.range_min < value < device.range_max for value in inside)
    # Readings move rather than repeat one value.
    assert len(set(inside)) > 50


def test_values_follow_an_admin_range():
    device = DEVICES[0]
    category_range = CategoryRange(min=-1000.0, max=-900.0)
    values = _sample(device, category_range, 10_000)
    out = sum(is_out_of_range(value, category_range) for value in values)
    assert 0.04 <= out / len(values) <= 0.06
    assert all(-1200 < value < -700 for value in values)


def test_no_range_means_values_in_the_default_band_and_nothing_judged():
    device = DEVICES[1]
    values = _sample(device, None, 5_000)
    assert all(device.range_min < value < device.range_max for value in values)
    assert not any(is_out_of_range(value, None) for value in values)


def test_a_very_narrow_range_still_gives_correct_sides():
    device = DEVICES[2]  # Rounds to whole numbers.
    category_range = CategoryRange(min=10.0, max=10.4)
    values = _sample(device, category_range, 5_000)
    out = sum(is_out_of_range(value, category_range) for value in values)
    assert 0.03 <= out / len(values) <= 0.07
    assert all(10.0 < value < 10.4 for value in values if not is_out_of_range(value, category_range))


# --- Interval setting (PRD-07-R1) ---


def test_interval_defaults_to_one_second():
    assert load_settings().simulator_interval_seconds == 1


@pytest.mark.parametrize("raw, expected", [("2", 2.0), ("0.1", 0.1), ("3600", 3600.0)])
def test_interval_can_be_set(monkeypatch, raw, expected):
    monkeypatch.setenv("SIMULATOR_INTERVAL_SECONDS", raw)
    assert load_settings().simulator_interval_seconds == expected


@pytest.mark.parametrize("raw", ["0", "0.05", "-1", "3601", "nan", "abc"])
def test_interval_rejects_bad_values(monkeypatch, raw):
    monkeypatch.setenv("SIMULATOR_INTERVAL_SECONDS", raw)
    with pytest.raises(ConfigurationError, match="SIMULATOR_INTERVAL_SECONDS"):
        load_settings()


# --- Live window ---


def _hub(interval=30.0):
    return RealtimeHub(session_factory=None, interval_seconds=interval)


def _reading(category, value, at, anomaly=False):
    device = next(d for d in DEVICES if d.category == category)
    return Reading(category, device.title, value, device.unit, CategoryRange(min=0, max=100), anomaly, at)


def test_window_keeps_five_minutes_and_counts_anomalies():
    hub = _hub()
    now = utc_now()
    hub.publish([_reading("溫度", 50, now - timedelta(seconds=WINDOW_SECONDS + 5), anomaly=True)], now)
    hub.publish([_reading("溫度", 150, now, anomaly=True), _reading("壓力", 40, now)], now)
    snapshot = hub.snapshot()
    by_category = {c.category: c for c in snapshot.categories}
    assert [p.value for p in by_category["溫度"].readings] == [150]
    assert by_category["溫度"].anomaly_count_5m == 1
    assert snapshot.summary.anomaly_count_5m == 1
    assert snapshot.summary.anomalous_category_count == 1
    assert snapshot.summary.category_count == 4
    assert snapshot.summary.stalled is False
    assert snapshot.stale_after_seconds == 70
    # The old anomaly left the window but stays in the recent list, newest first.
    assert [a.value for a in snapshot.recent_anomalies] == [150, 50]


def test_stalled_after_two_intervals_plus_ten_seconds():
    hub = _hub(interval=1.0)
    old = utc_now() - timedelta(seconds=13)
    hub.publish([_reading("振動", 3, old)], old)
    assert hub.snapshot().summary.stalled is True
    assert _hub().snapshot().summary.stalled is False  # No data yet is "waiting", not stalled.


def test_slow_subscriber_is_dropped_with_1013():
    hub = _hub()
    subscriber = hub.subscribe(1, "s", datetime.now(UTC) + timedelta(hours=1))
    now = utc_now()
    for _ in range(subscriber.queue.maxsize + 1):
        hub.publish([_reading("溫度", 50, now)], now)
    assert subscriber.closing
    items = [subscriber.queue.get_nowait() for _ in range(subscriber.queue.qsize())]
    assert items[-1].code == 1013


# --- Custom range (PRD-08-R13) ---

TAIPEI = timezone(timedelta(hours=8))


def _params(start, end):
    return RangeParams(start=start.isoformat(), end=end.isoformat())


@pytest.mark.parametrize(
    "start, expected",
    [
        (datetime(2026, 1, 15, 8, 30, tzinfo=TAIPEI), datetime(2026, 4, 15, 8, 30, tzinfo=TAIPEI)),
        (datetime(2025, 11, 30, tzinfo=UTC), datetime(2026, 2, 28, tzinfo=UTC)),
        (datetime(2027, 11, 30, tzinfo=UTC), datetime(2028, 2, 29, tzinfo=UTC)),
        (datetime(2026, 10, 31, tzinfo=UTC), datetime(2027, 1, 31, tzinfo=UTC)),
    ],
)
def test_three_calendar_months(start, expected):
    assert add_months(start, 3) == expected


def test_exactly_three_months_is_allowed_and_one_second_more_is_not():
    start = datetime(2026, 8, 7, 9, 0, tzinfo=TAIPEI)
    check_range(_params(start, datetime(2026, 11, 7, 9, 0, tzinfo=TAIPEI)))
    with pytest.raises(RangeTooLongError) as caught:
        check_range(_params(start, datetime(2026, 11, 7, 9, 0, 1, tzinfo=TAIPEI)))
    assert caught.value.details["max_months"] == 3
    assert caught.value.message == "時間範圍超過 3 個月上限。請縮短範圍，或分批查詢。"


def test_range_returns_naive_utc():
    start, end = check_range(
        _params(datetime(2026, 1, 1, 8, tzinfo=TAIPEI), datetime(2026, 1, 1, 9, tzinfo=TAIPEI))
    )
    assert (start, end) == (datetime(2026, 1, 1, 0), datetime(2026, 1, 1, 1))


def test_bucket_choice_keeps_at_most_400_points():
    assert choose_bucket(4 * 3600) == 60
    assert choose_bucket(2 * 86400) == 900
    assert choose_bucket(92 * 86400) == 21600
    for seconds in BUCKET_CHOICES:
        assert 86400 % seconds == 0
    assert [bucket_label(s) for s in (None, 60, 900, 3600, 21600, 86400)] == [
        "逐筆", "每 1 分鐘", "每 15 分鐘", "每 1 小時", "每 6 小時", "每 1 天"
    ]
