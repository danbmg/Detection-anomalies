"""Marque les points qui sortent de leur plage normale, avec leur z-score."""

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
    label: Kind | None  # vérité terrain si connue : montre d'un coup d'œil si le flag est juste


def z_score(value: float, band: Band) -> float:
    """Distance à la valeur attendue, comptée en écarts-types."""
    gap = value - band.expected
    if band.std == 0:  # historique plat : aucun changement est normal, tout changement est infini
        return 0.0 if gap == 0 else math.copysign(math.inf, gap)
    return gap / band.std


def flag_deviations(
    points: Sequence[Point], window: int = DEFAULT_WINDOW, k: float = DEFAULT_K
) -> list[Flag]:
    """Points dont |z| dépasse k, donc hors de leur bande. Jamais ceux en chauffe."""
    bands = rolling_bands([point.value for point in points], window, k)
    flags = []
    for point, band in zip(points, bands, strict=True):
        if band is None:
            continue
        z = z_score(point.value, band)
        if abs(z) > k:
            flags.append(Flag(point.timestamp, point.value, band.expected, z, point.label))
    return flags
