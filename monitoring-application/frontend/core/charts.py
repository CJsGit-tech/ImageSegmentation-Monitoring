"""Altair charts for SCREEN-20 (CH-01 to CH-05). Builders take plain rows and return a chart.

CH-04: an anomaly is warm red, a diamond and the word 異常 together, so it reads in greyscale.
CH-05: `dimmed` lowers the contrast of the data while updates have stopped; it never adds red.
Times arrive preformatted for tooltips: the screen formats them in the browser's timezone.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import altair as alt
import pandas as pd

ANOMALY = "#C62F26"
LINE = "#46586C"
BAND = "#E3E8F0"
BOUND = "#7D8A9B"
TRACK = "#EEF1F5"
UNSET = "#9A5B00"
SPREAD = "#C9D3DF"
BOUND_LABEL = "異常界線（含）"
_DIMMED_OPACITY = 0.4
_ROW_HEIGHT = 34
_CHART_HEIGHT = 170
_POSITION_MIN, _POSITION_MAX, _TEXT_AT = -0.25, 1.95, 1.32


@dataclass(frozen=True)
class Bounds:
    low: float
    high: float


def bounds_of(range_: dict[str, float] | None) -> Bounds | None:
    return Bounds(float(range_["min"]), float(range_["max"])) if range_ else None


def _y_domain(values: list[float], bounds: Bounds | None) -> list[float]:
    """Room for the whole band and every value, so the band always shows."""
    candidates = list(values) + ([bounds.low, bounds.high] if bounds else [])
    if not candidates:
        return [0.0, 1.0]
    low, high = min(candidates), max(candidates)
    pad = (high - low) * 0.18 or abs(high) * 0.1 or 1.0
    return [low - pad, high + pad]


def _y_channel(values: list[float], bounds: Bounds | None) -> Callable[[str], alt.Y]:
    """One y scale for every layer of a chart: each layer names its own field."""
    scale = alt.Scale(domain=_y_domain(values, bounds), nice=False, zero=False)
    axis = alt.Axis(values=[bounds.low, bounds.high] if bounds else alt.Undefined, tickCount=3, title=None, format="~r")
    return lambda name: alt.Y(f"{name}:Q", scale=scale, axis=axis)


def _band_layers(bounds: Bounds | None, x_start: Any, x_end: Any, x_of: Callable[[str], alt.X], y: Callable[[str], alt.Y]) -> list[alt.Chart]:
    """Every layer uses the chart's own x channel, so the layers share one scale."""
    if bounds is None:
        return []
    frame = pd.DataFrame([{"x": x_start, "x2": x_end, "low": bounds.low, "high": bounds.high}])
    base = alt.Chart(frame)
    x = x_of("x")
    band = base.mark_rect(color=BAND).encode(x=x, x2="x2", y=y("low"), y2="high")
    edges = pd.DataFrame(
        [
            {"x": x_start, "y": bounds.high, "label": BOUND_LABEL, "baseline": "bottom"},
            {"x": x_start, "y": bounds.low, "label": BOUND_LABEL, "baseline": "top"},
        ]
    )
    rules = alt.Chart(edges).mark_rule(color=BOUND, strokeDash=[5, 4], strokeWidth=1.2).encode(y=y("y"))
    top = alt.Chart(edges.iloc[:1]).mark_text(align="left", baseline="bottom", dx=4, dy=-2, color=BOUND, fontSize=10)
    bottom = alt.Chart(edges.iloc[1:]).mark_text(align="left", baseline="top", dx=4, dy=2, color=BOUND, fontSize=10)
    labels = [layer.encode(x=x, y=y("y"), text="label:N") for layer in (top, bottom)]
    return [band, rules, *labels]


def _anomaly_layers(frame: pd.DataFrame, x: alt.X, y_field: str, y: Callable[[str], alt.Y], tooltip: list, opacity: float) -> list:
    marks = frame[frame["anomaly"]]
    if marks.empty:
        return []
    diamonds = (
        alt.Chart(marks)
        .mark_point(shape="diamond", filled=True, size=110, color=ANOMALY, opacity=opacity, stroke="white", strokeWidth=0.8)
        .encode(x=x, y=y(y_field), tooltip=tooltip)
    )
    return [diamonds]


def _finish(layers: list, height: int, alt_text: str) -> alt.LayerChart:
    return (
        alt.layer(*layers)
        .properties(height=height, description=alt_text)
        .configure_view(stroke=None)
        .configure_axis(grid=False, labelColor="#5B6878", titleColor="#5B6878", domainColor="#C9D0DA", tickColor="#C9D0DA")
    )


def live_chart(
    rows: list[dict[str, Any]], bounds: Bounds | None, *, window_seconds: int, dimmed: bool, title: str
) -> alt.LayerChart:
    """CH-01 live: rows are {offset (seconds before now, <= 0), value, anomaly, time, value_text, status}."""
    opacity = _DIMMED_OPACITY if dimmed else 1.0
    frame = pd.DataFrame(rows, columns=["offset", "value", "anomaly", "time", "value_text", "status"])
    frame["anomaly"] = frame["anomaly"].astype(bool)
    half = window_seconds // 2
    label = (
        "datum.value == 0 ? '現在' : '-' + floor(abs(datum.value) / 60) + ':' "
        "+ (abs(datum.value) % 60 < 10 ? '0' : '') + (abs(datum.value) % 60)"
    )
    x_scale = alt.Scale(domain=[-window_seconds, 0], nice=False)
    x_axis = alt.Axis(values=[-window_seconds, -half, 0], labelExpr=label, title=None)

    def x_of(name: str) -> alt.X:
        return alt.X(f"{name}:Q", scale=x_scale, axis=x_axis)

    x = x_of("offset")
    y = _y_channel(frame["value"].tolist(), bounds)
    tooltip = [alt.Tooltip("time:N", title="時間"), alt.Tooltip("value_text:N", title="讀數"), alt.Tooltip("status:N", title="狀態")]
    layers = _band_layers(bounds, -window_seconds, 0, x_of, y)
    if not frame.empty:
        base = alt.Chart(frame)
        layers.append(base.mark_line(color=LINE, strokeWidth=1.6, opacity=opacity).encode(x=x, y=y("value"), tooltip=tooltip))
        # Small dots keep a lone reading visible, which a line alone cannot draw; they also take the tooltip.
        layers.append(
            base.mark_point(size=16, filled=True, color=LINE, opacity=opacity).encode(x=x, y=y("value"), tooltip=tooltip)
        )
        layers += _anomaly_layers(frame, x, "value", y, tooltip, opacity)
        last = frame.iloc[[-1]]
        if bool(last["anomaly"].iloc[0]):
            note = last.assign(note="異常 " + last["value_text"])
            layers.append(
                alt.Chart(note)
                .mark_text(align="right", baseline="bottom", dx=-6, dy=-8, color=ANOMALY, fontWeight="bold", fontSize=11, opacity=opacity)
                .encode(x=x, y=y("value"), text="note:N")
            )
    return _finish(layers, _CHART_HEIGHT, f"{title}最近 {window_seconds // 60} 分鐘的讀數折線圖")


def range_chart(
    rows: list[dict[str, Any]], bounds: Bounds | None, *, start_ms: int, end_ms: int, aggregated: bool, title: str
) -> alt.LayerChart:
    """CH-01 saved data. rows: {t (wall-clock ms as UTC), avg, min, max, mark (y of the anomaly end),
    anomaly, time, avg_text, spread_text, count, anomaly_count}. Times are wall clock on a UTC scale,
    so Vega labels them as the viewer's clock without shifting them again."""
    columns = ["t", "avg", "min", "max", "mark", "anomaly", "time", "avg_text", "spread_text", "count", "anomaly_count"]
    frame = pd.DataFrame(rows, columns=columns)
    frame["anomaly"] = frame["anomaly"].astype(bool)
    # Floats: an int64 column reaches the browser as a BigInt, which Vega cannot read as a time.
    frame["t"] = frame["t"].astype(float)
    start_ms, end_ms = float(start_ms), float(end_ms)
    x_scale = alt.Scale(type="utc", domain=[start_ms, end_ms], nice=False)
    # 24-hour labels; Vega's default multi-format would mix in "09 PM".
    span_days = (end_ms - start_ms) / 86_400_000
    time_format = "%H:%M" if span_days <= 1 else ("%m-%d %H:%M" if span_days <= 7 else "%Y-%m-%d")
    x_axis = alt.Axis(title=None, tickCount=5, format=time_format, labelOverlap=True)

    def x_of(name: str) -> alt.X:
        return alt.X(f"{name}:T", scale=x_scale, axis=x_axis)

    x = x_of("t")
    values = frame["min"].tolist() + frame["max"].tolist()
    y = _y_channel(values, bounds)
    if aggregated:
        tooltip = [
            alt.Tooltip("time:N", title="區間開始"),
            alt.Tooltip("avg_text:N", title="平均"),
            alt.Tooltip("spread_text:N", title="最小 – 最大"),
            alt.Tooltip("count:Q", title="筆數"),
            alt.Tooltip("anomaly_count:Q", title="異常筆數"),
        ]
    else:
        tooltip = [alt.Tooltip("time:N", title="時間"), alt.Tooltip("avg_text:N", title="讀數"), alt.Tooltip("anomaly_count:Q", title="異常")]
    layers = _band_layers(bounds, start_ms, end_ms, x_of, y)
    if not frame.empty:
        base = alt.Chart(frame)
        if aggregated:
            layers.append(base.mark_area(color=SPREAD, opacity=0.55).encode(x=x, y=y("min"), y2="max"))
        layers.append(base.mark_line(color=LINE, strokeWidth=1.6).encode(x=x, y=y("avg"), tooltip=tooltip))
        layers.append(base.mark_point(size=12, filled=True, color=LINE).encode(x=x, y=y("avg"), tooltip=tooltip))
        layers += _anomaly_layers(frame, x, "mark", y, tooltip, 1.0)
    return _finish(layers, _CHART_HEIGHT, f"{title}在所選時間範圍的讀數折線圖")


def trend_chart(
    rows: list[dict[str, Any]], marks: list[dict[str, Any]], bounds: Bounds | None, *, start_ms: int, end_ms: int, title: str
) -> alt.LayerChart:
    """CH-01 for SCREEN-30 trends. rows: {t (wall-clock ms as UTC), avg, min, max, time, avg_text,
    spread_text, status}, one per bucket. marks: {t, value, time, value_text}, one per real anomalous
    extreme (PRD-11-R9), so an averaged bucket still shows where its anomalies were. No counts."""
    frame = pd.DataFrame(rows, columns=["t", "avg", "min", "max", "time", "avg_text", "spread_text", "status"])
    frame["t"] = frame["t"].astype(float)
    mark_frame = pd.DataFrame(marks, columns=["t", "value", "time", "value_text"])
    mark_frame["t"] = mark_frame["t"].astype(float)
    mark_frame["anomaly"] = True
    mark_frame["status"] = "◆ 異常"
    start_ms, end_ms = float(start_ms), float(end_ms)
    x_scale = alt.Scale(type="utc", domain=[start_ms, end_ms], nice=False)
    span_days = (end_ms - start_ms) / 86_400_000
    time_format = "%H:%M" if span_days <= 1 else ("%m-%d %H:%M" if span_days <= 3 else "%m-%d")
    x_axis = alt.Axis(title=None, tickCount=6, format=time_format, labelOverlap=True)

    def x_of(name: str) -> alt.X:
        return alt.X(f"{name}:T", scale=x_scale, axis=x_axis)

    x = x_of("t")
    y = _y_channel(frame["min"].tolist() + frame["max"].tolist() + mark_frame["value"].tolist(), bounds)
    tooltip = [
        alt.Tooltip("time:N", title="區間開始"),
        alt.Tooltip("avg_text:N", title="平均"),
        alt.Tooltip("spread_text:N", title="最小 – 最大"),
        alt.Tooltip("status:N", title="狀態"),
    ]
    mark_tooltip = [alt.Tooltip("time:N", title="區間開始"), alt.Tooltip("value_text:N", title="異常數值"), alt.Tooltip("status:N", title="狀態")]
    layers = _band_layers(bounds, start_ms, end_ms, x_of, y)
    if not frame.empty:
        base = alt.Chart(frame)
        layers.append(base.mark_area(color=SPREAD, opacity=0.55).encode(x=x, y=y("min"), y2="max"))
        layers.append(base.mark_line(color=LINE, strokeWidth=1.6).encode(x=x, y=y("avg"), tooltip=tooltip))
        layers.append(base.mark_point(size=12, filled=True, color=LINE).encode(x=x, y=y("avg"), tooltip=tooltip))
        layers += _anomaly_layers(mark_frame, x, "value", y, mark_tooltip, 1.0)
    return _finish(layers, _CHART_HEIGHT, f"{title}在所選時間範圍的趨勢折線圖，異常以菱形標示")


def position_bars(rows: list[dict[str, Any]]) -> alt.LayerChart:
    """CH-02: rows are {name, position (None without a range or reading), anomaly, value_text, note}. Position 0 is
    the minimum and 1 the maximum; outside the band the bar runs past it and turns red."""
    frame = pd.DataFrame(rows, columns=["name", "position", "anomaly", "value_text", "note"])
    frame["anomaly"] = frame["anomaly"].astype(bool)
    order = frame["name"].tolist()
    y = alt.Y("name:N", sort=order, axis=alt.Axis(title=None, labelFontSize=13, labelColor="#1E2A36", ticks=False, domain=False, labelPadding=10))
    x_scale = alt.Scale(domain=[_POSITION_MIN, _POSITION_MAX], nice=False)
    ranged = frame[frame["position"].notna()].copy()
    ranged["shown"] = ranged["position"].clip(_POSITION_MIN + 0.03, 1.22)
    ranged["zero"] = 0.0
    ranged["one"] = 1.0
    ranged["track_start"] = -0.2
    ranged["track_end"] = 1.22
    ranged["text"] = ranged.apply(lambda row: ("◆ 異常 " if row["anomaly"] else "") + row["value_text"], axis=1)
    unset = frame[frame["position"].isna()].copy()
    unset["text"] = unset["value_text"]
    color = alt.condition("datum.anomaly", alt.value(ANOMALY), alt.value(LINE))
    layers: list[Any] = []
    if not ranged.empty:
        base = alt.Chart(ranged)
        layers += [
            base.mark_bar(color=TRACK, height=18).encode(x=alt.X("track_start:Q", scale=x_scale, axis=None), x2="track_end", y=y),
            base.mark_bar(color=BAND, height=18).encode(x=alt.X("zero:Q", scale=x_scale, axis=None), x2="one", y=y),
            base.mark_tick(color=BOUND, thickness=1, size=22, strokeDash=[3, 2]).encode(x=alt.X("zero:Q", scale=x_scale, axis=None), y=y),
            base.mark_tick(color=BOUND, thickness=1, size=22, strokeDash=[3, 2]).encode(x=alt.X("one:Q", scale=x_scale, axis=None), y=y),
            base.mark_bar(height=6).encode(x=alt.X("zero:Q", scale=x_scale, axis=None), x2="shown", y=y, color=color),
            base.mark_tick(thickness=4, size=20).encode(x=alt.X("shown:Q", scale=x_scale, axis=None), y=y, color=color),
            base.transform_filter("datum.anomaly")
            .mark_point(shape="diamond", filled=True, size=120, color=ANOMALY, stroke="white", strokeWidth=0.8)
            .encode(x=alt.X("shown:Q", scale=x_scale, axis=None), y=y),
            base.mark_text(align="left", fontSize=13, dx=0)
            .encode(x=alt.datum(_TEXT_AT, scale=x_scale), y=y, text="text:N", color=color),
        ]
    if not unset.empty:
        base = alt.Chart(unset)
        layers += [
            base.mark_text(align="left", fontSize=12, fontWeight="bold", color=UNSET).encode(x=alt.datum(-0.2, scale=x_scale), y=y, text="note:N"),
            base.mark_text(align="left", fontSize=13, color=LINE).encode(x=alt.datum(_TEXT_AT, scale=x_scale), y=y, text="text:N"),
        ]
    return _finish(layers, _ROW_HEIGHT * max(len(frame), 1) + 8, "各分類最新讀數在最小值到最大值之間的位置")


def anomaly_count_bars(rows: list[dict[str, Any]]) -> alt.LayerChart:
    """CH-03: rows are {name, count}. A count of 0 keeps the neutral empty track."""
    frame = pd.DataFrame(rows, columns=["name", "count"])
    top = max(int(frame["count"].max()) if not frame.empty else 0, 1)
    end = top * 1.18
    frame["zero"] = 0
    frame["track"] = top
    frame["text_at"] = end
    order = frame["name"].tolist()
    y = alt.Y("name:N", sort=order, axis=alt.Axis(title=None, labelFontSize=13, labelColor="#1E2A36", ticks=False, domain=False, labelPadding=10))
    x_scale = alt.Scale(domain=[0, end], nice=False)
    base = alt.Chart(frame)
    layers = [
        base.mark_bar(color=TRACK, height=16).encode(x=alt.X("zero:Q", scale=x_scale, axis=None), x2="track", y=y),
        base.transform_filter("datum.count > 0")
        .mark_bar(color=ANOMALY, height=16)
        .encode(x=alt.X("zero:Q", scale=x_scale, axis=None), x2="count", y=y, tooltip=[alt.Tooltip("name:N", title="分類"), alt.Tooltip("count:Q", title="異常次數")]),
        base.mark_text(align="right", fontSize=13, fontWeight="bold", color="#1E2A36").encode(x=alt.X("text_at:Q", scale=x_scale, axis=None), y=y, text="count:Q"),
    ]
    return _finish(layers, _ROW_HEIGHT * max(len(frame), 1) + 8, "各分類的異常次數橫條圖")
