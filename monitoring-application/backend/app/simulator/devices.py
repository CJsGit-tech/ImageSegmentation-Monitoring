"""The four simulated devices, their startup defaults (PRD-07-R7, R8, R11) and value model.

Names, units and ranges follow the approved M3 visuals. A device's category is the records.category
value it writes and the key of its range and unit setting.
"""

from dataclasses import dataclass

from app.categories.seed import CategoryDefault


@dataclass(frozen=True)
class Device:
    category: str
    title: str
    unit: str
    range_min: float
    range_max: float
    # Where readings centre, as a fraction of the range, and how much they move per step,
    # as a fraction of the range. Both scale with whatever range is in force.
    base_fraction: float
    step_fraction: float
    decimals: int

    @property
    def default(self) -> CategoryDefault:
        return CategoryDefault(self.category, self.range_min, self.range_max, self.unit)


def _device(category: str, title: str, unit: str, low: float, high: float, base: float, amp: float, decimals: int) -> Device:
    span = high - low
    return Device(category, title, unit, low, high, (base - low) / span, amp / span, decimals)


DEVICES: tuple[Device, ...] = (
    _device("溫度", "溫度感測器 T-01", "°C", 20, 80, 55, 9, 1),
    _device("壓力", "壓力感測器 P-01", "kPa", 100, 500, 300, 55, 0),
    _device("馬達轉速", "馬達轉速計 R-01", "rpm", 1000, 3000, 2000, 320, 0),
    _device("振動", "振動感測器 V-01", "mm/s", 0.5, 8, 3.6, 1.1, 1),
)

DEVICE_DEFAULTS: tuple[CategoryDefault, ...] = tuple(device.default for device in DEVICES)
DEVICE_CATEGORIES: tuple[str, ...] = tuple(device.category for device in DEVICES)
