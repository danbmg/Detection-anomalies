"""Marque les points anormaux : hors de leur plage normale (z-score), ou en dérive lente (CUSUM)."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from anomaly.model import Kind, Point
from anomaly.normal import DEFAULT_K, Band, Method, normal_bands

# Écart, en écarts-types, que CUSUM laisse passer à chaque point sans rien accumuler : le bruit
# tombe des deux côtés et s'annule, une dérive d'au moins un demi écart-type s'additionne.
DRIFT_SLACK = 0.5
# Somme qui déclenche. Sur 10 séries simulées, 10 donnait encore une fausse alarme, 15 aucune,
# et repère la dérive injectée 7 à 12 heures après son début.
DRIFT_THRESHOLD = 15.0


@dataclass(frozen=True)
class Flag:
    timestamp: datetime
    value: float
    expected: float
    z: float
    label: Kind | None  # vérité terrain si connue : montre d'un coup d'œil si le flag est juste
    cusum: float | None = None  # somme CUSUM d'une alarme de dérive (+ en hausse, - en baisse)

    @property
    def kind(self) -> Kind:
        """Ce que le détecteur a vu : pic au-dessus de la bande, chute en dessous, ou dérive."""
        if self.cusum is not None:
            return Kind.DRIFT
        return Kind.SPIKE if self.z > 0 else Kind.DROP


def z_score(value: float, band: Band) -> float:
    """Distance à la valeur attendue, comptée en écarts-types."""
    gap = value - band.expected
    if band.std == 0:  # historique plat : aucun changement est normal, tout changement est infini
        return 0.0 if gap == 0 else math.copysign(math.inf, gap)
    return gap / band.std


def flag_deviations(
    points: Sequence[Point],
    window: int | None = None,
    k: float = DEFAULT_K,
    method: Method = Method.ROLLING,
) -> list[Flag]:
    """Points dont |z| dépasse k, donc hors de leur bande. Jamais ceux en chauffe."""
    bands = normal_bands(points, method, window, k)
    flags = []
    for point, band in zip(points, bands, strict=True):
        if band is None:
            continue
        z = z_score(point.value, band)
        if abs(z) > k:
            flags.append(Flag(point.timestamp, point.value, band.expected, z, point.label))
    return flags


def flag_drifts(
    points: Sequence[Point],
    bands: Sequence[Band | None],
    slack: float = DRIFT_SLACK,
    threshold: float = DRIFT_THRESHOLD,
) -> list[Flag]:
    """Alarmes CUSUM : les petits écarts d'un même côté, additionnés, dépassent `threshold`.

    Une dérive lente reste dans sa bande, donc aucun z-score ne la voit, mais elle tire tous les
    points du même côté. Il faut une valeur attendue qui ne la suit pas : la saisonnière, pas la
    moyenne glissante. Les points hors bande restent au z-score : sinon un pic ferait une dérive.
    """
    if slack < 0 or threshold <= 0:
        raise ValueError(f"slack must be >= 0 and threshold > 0, got {slack} and {threshold}")
    above = below = 0.0  # les deux sommes : dérive vers le haut, dérive vers le bas
    flags = []
    for point, band in zip(points, bands, strict=True):
        if band is None:
            continue
        z = z_score(point.value, band)
        if abs(z) > band.k:
            continue
        above = max(0.0, above + z - slack)  # max(0, …) : une somme ne descend jamais sous 0
        below = max(0.0, below - z - slack)
        if above > threshold or below > threshold:
            cusum = above if above > threshold else -below
            flags.append(Flag(point.timestamp, point.value, band.expected, z, point.label, cusum))
            above = below = 0.0  # on repart de 0 : une dérive qui dure redéclenche plus loin
    return flags
