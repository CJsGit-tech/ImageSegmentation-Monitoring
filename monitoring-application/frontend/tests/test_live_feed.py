import pytest

from core import live_feed

NOW = "2026-01-01T00:10:00Z"


def snapshot(**categories):
    """A LiveSnapshot-shaped dict with a 5-minute window and one category per keyword."""
    return {
        "server_time": "2026-01-01T00:09:00Z",
        "window_seconds": 300,
        "stale_after_seconds": 30,
        "generator": {"running": True},
        "summary": {"last_reading_at": "2026-01-01T00:09:00Z", "stalled": False},
        "recent_anomalies": [],
        "categories": [
            {"category": name, "readings": readings, "latest": None, "anomaly_count_5m": 0}
            for name, readings in categories.items()
        ],
    }


def reading(category="溫度", value=5.0, timestamp=NOW, is_anomaly=False, range_=None):
    return {
        "category": category,
        "title": f"{category}感測器",
        "value": value,
        "timestamp": timestamp,
        "is_anomaly": is_anomaly,
        "range": range_,
        "unit": "°C",
    }


def message(*readings, **extra):
    return {"server_time": NOW, "summary": {"total": 1}, "readings": list(readings), **extra}


def point(timestamp):
    return {"value": 1.0, "timestamp": timestamp, "is_anomaly": False}


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        ("http://backend:8000", "ws://backend:8000/api/v1/realtime/ws"),
        ("https://example.test/", "wss://example.test/api/v1/realtime/ws"),
    ],
)
def test_ws_url_matches_the_backends_scheme(monkeypatch, backend, expected):
    monkeypatch.setenv("BACKEND_URL", backend)

    assert live_feed.ws_url() == expected


def test_readings_update_the_category():
    bounds = {"min": 0.0, "max": 10.0}
    current = snapshot(溫度=[])

    live_feed.merge_readings(current, message(reading(range_=bounds)))

    category = current["categories"][0]
    assert category["readings"] == [{"value": 5.0, "timestamp": NOW, "is_anomaly": False}]
    assert category["latest"] == category["readings"][0]
    assert category["range"] == bounds
    assert category["range_status"] == "set"
    assert category["unit"] == "°C"
    assert current["server_time"] == NOW
    assert current["summary"] == {"total": 1}


def test_reading_without_a_range_marks_it_unset():
    current = snapshot(溫度=[])

    live_feed.merge_readings(current, message(reading()))

    assert current["categories"][0]["range_status"] == "unset"


def test_anomaly_counts_update_and_keep_the_old_value_when_missing():
    current = snapshot(溫度=[], 濕度=[])
    current["categories"][1]["anomaly_count_5m"] = 3

    live_feed.merge_readings(current, message(category_anomaly_counts_5m={"溫度": 2}))

    assert [c["anomaly_count_5m"] for c in current["categories"]] == [2, 3]


def test_new_anomalies_come_first_newest_first():
    current = snapshot(溫度=[])
    current["recent_anomalies"] = [{"title": "舊"}]
    older = reading(timestamp="2026-01-01T00:09:58Z", is_anomaly=True)
    newer = reading(timestamp="2026-01-01T00:09:59Z", is_anomaly=True)

    live_feed.merge_readings(current, message(older, newer))

    assert [a["timestamp"] for a in current["recent_anomalies"][:2]] == [newer["timestamp"], older["timestamp"]]
    assert current["recent_anomalies"][2] == {"title": "舊"}


def test_recent_anomalies_stay_within_the_limit():
    current = snapshot(溫度=[])
    current["recent_anomalies"] = [{"title": str(i)} for i in range(live_feed.RECENT_ANOMALY_LIMIT)]

    live_feed.merge_readings(current, message(reading(is_anomaly=True)))

    assert len(current["recent_anomalies"]) == live_feed.RECENT_ANOMALY_LIMIT
    assert current["recent_anomalies"][-1] == {"title": str(live_feed.RECENT_ANOMALY_LIMIT - 2)}


def test_anomaly_in_an_unknown_category_is_still_listed():
    current = snapshot(溫度=[])

    live_feed.merge_readings(current, message(reading(category="新分類", is_anomaly=True)))

    assert current["categories"][0]["readings"] == []
    assert current["recent_anomalies"][0]["category"] == "新分類"


def test_merge_drops_points_that_left_the_window():
    # The window is 300 s, so at 00:10:00 the cutoff is 00:05:00 and that exact point stays.
    kept, dropped = point("2026-01-01T00:05:00Z"), point("2026-01-01T00:04:59Z")
    current = snapshot(溫度=[dropped, kept])

    live_feed.merge_readings(current, message())

    assert current["categories"][0]["readings"] == [kept]


def heartbeat(last, server_time=NOW):
    return {"server_time": server_time, "generator": {"running": False}, "last_reading_at": last}


def test_heartbeat_updates_time_and_generator():
    current = snapshot(溫度=[])

    live_feed.merge_heartbeat(current, heartbeat("2026-01-01T00:09:59Z"))

    assert current["server_time"] == NOW
    assert current["generator"] == {"running": False}
    assert current["summary"]["last_reading_at"] == "2026-01-01T00:09:59Z"


@pytest.mark.parametrize(
    ("last", "stalled"),
    [("2026-01-01T00:09:30Z", False), ("2026-01-01T00:09:29Z", True)],
)
def test_heartbeat_marks_stalled_only_past_the_limit(last, stalled):
    current = snapshot(溫度=[])

    live_feed.merge_heartbeat(current, heartbeat(last))

    assert current["summary"]["stalled"] is stalled


def test_heartbeat_without_a_time_uses_the_last_known_reading():
    current = snapshot(溫度=[])

    live_feed.merge_heartbeat(current, heartbeat(None))

    assert current["summary"]["last_reading_at"] == "2026-01-01T00:09:00Z"
    assert current["summary"]["stalled"] is True


def test_heartbeat_with_no_readings_yet_leaves_stalled_alone():
    current = snapshot(溫度=[])
    current["summary"] = {"last_reading_at": None, "stalled": False}

    live_feed.merge_heartbeat(current, heartbeat(None))

    assert current["summary"]["stalled"] is False


def test_heartbeat_also_drops_old_points():
    current = snapshot(溫度=[point("2026-01-01T00:04:59Z")])

    live_feed.merge_heartbeat(current, heartbeat(NOW))

    assert current["categories"][0]["readings"] == []
