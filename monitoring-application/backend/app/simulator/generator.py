"""The value model for one simulated device. Pure and seedable, so tests can sample it fast."""

import random

from app.records.anomaly import CategoryRange, is_out_of_range
from app.simulator.devices import Device

# PRD-07-R9: about 5% of readings fall outside the range.
ANOMALY_PROBABILITY = 0.05
# How strongly a normal reading is pulled back towards the base each step (0: none, 1: stays put).
REVERSION = 0.7
# An out-of-range reading lands this far beyond a bound, as a fraction of the range.
ANOMALY_OFFSET = (0.02, 0.15)


class ValueModel:
    """Readings follow a mean-reverting random walk inside the range, never touching a bound,
    plus independent out-of-range readings with ANOMALY_PROBABILITY.

    Everything scales with the range in force, so an Admin's new range keeps the same share of
    anomalies. With no range (cleared), readings stay around the device's default band and none
    is out of range, because nothing is judged (PRD-09-R9).
    """

    def __init__(self, device: Device, rng: random.Random) -> None:
        self.device = device
        self.rng = rng
        self._previous: float | None = None

    def next_value(self, category_range: CategoryRange | None) -> float:
        device = self.device
        low, high = (
            (category_range.min, category_range.max)
            if category_range is not None
            else (device.range_min, device.range_max)
        )
        span = high - low
        if category_range is not None and self.rng.random() < ANOMALY_PROBABILITY:
            return self._outside(low, high, span)
        return self._inside(low, high, span)

    def _round(self, value: float) -> float:
        return round(value, self.device.decimals)

    def _inside(self, low: float, high: float, span: float) -> float:
        base = low + self.device.base_fraction * span
        step = self.device.step_fraction * span
        previous = self._previous if self._previous is not None and low < self._previous < high else base
        value = base + REVERSION * (previous - base) + self.rng.gauss(0, 0.5 * step)
        margin = max(0.01 * span, 10.0 ** -self.device.decimals)
        if low + margin < high - margin:
            value = min(max(value, low + margin), high - margin)
        else:
            value = low + span / 2
        rounded = self._round(value)
        # A very narrow range can make rounding land on a bound; keep the exact value then.
        value = value if is_out_of_range(rounded, CategoryRange(min=low, max=high)) else rounded
        self._previous = value
        return value

    def _outside(self, low: float, high: float, span: float) -> float:
        offset = self.rng.uniform(*ANOMALY_OFFSET) * span
        value = low - offset if self.rng.random() < 0.5 else high + offset
        rounded = self._round(value)
        return rounded if is_out_of_range(rounded, CategoryRange(min=low, max=high)) else value
