"""Flag the points that leave their normal range, with their z-score."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from anomaly.model import Kind, Point
from anomaly.normal import DEFAULT_K, DEFAULT_WINDOW, Band, rolling_bands


@dataclass(frozen=True)
class Flag:
    timestamp: datetime
    value: float
    expected: float
    z: float
    label: Kind | None  # ground truth when known: shows at a glance whether the flag is right


def z_score(value: float, band: Band) -> float:
    """Distance from the expected value, counted in standard deviations."""
    gap = value - band.expected
    if band.std == 0:  # flat history: no change is not unusual at all, any change is infinitely so
        return 0.0 if gap == 0 else math.copysign(math.inf, gap)
    return gap / band.std


def flag_deviations(
    points: Sequence[Point], window: int = DEFAULT_WINDOW, k: float = DEFAULT_K
) -> list[Flag]:
    """Points whose |z| is above k, i.e. outside their normal band. Warming-up points never are."""
    bands = rolling_bands([point.value for point in points], window, k)
    flags = []
    for point, band in zip(points, bands, strict=True):
        if band is None:
            continue
        z = z_score(point.value, band)
        if abs(z) > k:
            flags.append(Flag(point.timestamp, point.value, band.expected, z, point.label))
    return flags
