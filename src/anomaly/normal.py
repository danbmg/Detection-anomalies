"""Normal range of a metric, learned from its own past: rolling mean ± k x rolling std."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_WINDOW = 12  # previous points behind each band: 1 hour at one point every 5 minutes
DEFAULT_K = 3.0  # half-width of the band, in standard deviations


@dataclass(frozen=True)
class Band:
    expected: float  # mean of the previous points
    std: float  # their standard deviation: how far the metric usually strays from that mean
    k: float

    @property
    def lower(self) -> float:
        return self.expected - self.k * self.std

    @property
    def upper(self) -> float:
        return self.expected + self.k * self.std


def rolling_bands(
    values: Sequence[float], window: int = DEFAULT_WINDOW, k: float = DEFAULT_K
) -> list[Band | None]:
    """One band per value, computed from the `window` values just before it, never from later ones.

    The first `window` values get None: they are "warming up", without enough history to judge.
    """
    if window < 2:
        raise ValueError(f"window must be at least 2 points, got {window}")
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    bands: list[Band | None] = [None] * min(window, len(values))
    for i in range(window, len(values)):
        past = values[i - window : i]  # stops before i: a value is never part of its own band
        mean = sum(past) / window
        variance = sum((x - mean) ** 2 for x in past) / window
        bands.append(Band(mean, math.sqrt(variance), k))
    return bands
