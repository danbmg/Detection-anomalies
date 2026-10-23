"""Marque les points anormaux : hors de leur plage normale (z-score), ou en dérive lente (CUSUM)."""

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from anomaly.model import Kind, Point
from anomaly.normal import DEFAULT_K, Band, Method, normal_bands

# Écart, en écarts-types, que CUSUM laisse passer à chaque point sans rien accumuler : le bruit
# tombe des deux côtés et s'annule, une dérive d'au moins un demi écart-type s'additionne.
DRIFT_SLACK = 0.5
# Somme qui déclenche. Sur 10 séries simulées, 10 donnait encore une fausse alarme, 15 aucune,
# et repère la dérive injectée 7 à 12 heures après son début.
DRIFT_THRESHOLD = 15.0

# k adaptatif : recalculé toutes les heures, à partir des z-scores du dernier jour.
ADAPT_EVERY = 12
ADAPT_HISTORY = 288
# Jamais plus de 6 : les anomalies passeraient. Jamais moins de 2,5 : avec 2, k tombait au
# plancher après la dérive simulée (sa bande s'était élargie) et le bruit levait 3 fausses alertes.
K_LIMITS = (2.5, 6.0)
# Écart que dépassent 10 % des points quand le bruit suit la courbe en cloche, comme le suppose
# la bande. Si 10 % des points récents dépassent 2 x 1,645, le bruit est 2 fois celui prévu.
NORMAL_Q90 = 1.645


@dataclass(frozen=True)
class Flag:
    timestamp: datetime
    value: float
    expected: float
    z: float
    label: Kind | None  # vérité terrain si connue : montre d'un coup d'œil si le flag est juste
    cusum: float | None = None  # somme CUSUM d'une alarme de dérive (+ en hausse, - en baisse)
    k: float = DEFAULT_K  # seuil en vigueur à ce point, fixe ou adaptatif

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
    adaptive: bool = False,
) -> list[Flag]:
    """Points hors de leur bande, avec la méthode choisie et un k fixe ou adaptatif."""
    bands = normal_bands(points, method, window, k)
    return flag_outside(points, adapt_bands(points, bands) if adaptive else bands)


def check_point(point: Point, band: Band | None) -> Flag | None:
    """Un flag si |z| dépasse le k de la bande, donc si le point en sort. Jamais en chauffe."""
    if band is None:
        return None
    z = z_score(point.value, band)
    if abs(z) <= band.k:
        return None
    return Flag(point.timestamp, point.value, band.expected, z, point.label, k=band.k)


def flag_outside(points: Sequence[Point], bands: Sequence[Band | None]) -> list[Flag]:
    """Points dont |z| dépasse le k de leur bande, donc hors de celle-ci. Jamais ceux en chauffe."""
    flags = (check_point(point, band) for point, band in zip(points, bands, strict=True))
    return [flag for flag in flags if flag is not None]


class AdaptiveK:
    """Change le k de chaque nouvelle bande : recalculé tous les `every` points sur les `history`
    derniers z-scores, il monte d'autant que le bruit récent dépasse ce que la bande prévoit.

    Toujours dans `limits`. Sans `history` z-scores derrière, k reste celui de départ.
    """

    def __init__(
        self,
        every: int = ADAPT_EVERY,
        history: int = ADAPT_HISTORY,
        limits: tuple[float, float] = K_LIMITS,
    ) -> None:
        low, high = limits
        if every < 1 or history < 10 or not 0 < low <= high:
            raise ValueError(f"bad adaptive settings: every {every}, history {history}, {limits}")
        self.every, self.history, self.limits = every, history, limits
        self.recent: deque[float] = deque(maxlen=history)  # |z| des derniers points jugés
        self.noise = 1.0  # bruit récent / bruit prévu par la bande
        self.seen = 0  # points reçus, en chauffe compris : le rythme des recalculs

    def next(self, point: Point, band: Band | None) -> Band | None:
        if self.seen % self.every == 0 and len(self.recent) == self.history:
            # 90e centile plutôt que le maximum : les anomalies, rares, sont au-delà et le
            # déplacent à peine. Le maximum, lui, serait l'anomalie elle-même.
            q90 = sorted(self.recent)[int(0.9 * self.history)]
            self.noise = q90 / NORMAL_Q90
        self.seen += 1
        if band is None:
            return None
        low, high = self.limits
        adapted = replace(band, k=min(high, max(low, band.k * self.noise)))
        self.recent.append(abs(z_score(point.value, band)))  # après : jamais l'actuel dans son k
        return adapted


def adapt_bands(
    points: Sequence[Point],
    bands: Sequence[Band | None],
    every: int = ADAPT_EVERY,
    history: int = ADAPT_HISTORY,
    limits: tuple[float, float] = K_LIMITS,
) -> list[Band | None]:
    """Les mêmes bandes, avec un k adaptatif : voir AdaptiveK."""
    adaptive = AdaptiveK(every, history, limits)
    return [adaptive.next(point, band) for point, band in zip(points, bands, strict=True)]


class Cusum:
    """Alarmes de dérive : les petits écarts d'un même côté, additionnés, dépassent `threshold`.

    Une dérive lente reste dans sa bande, donc aucun z-score ne la voit, mais elle tire tous les
    points du même côté. Il faut une valeur attendue qui ne la suit pas : la saisonnière, pas la
    moyenne glissante. Les points hors bande restent au z-score : sinon un pic ferait une dérive.
    """

    def __init__(self, slack: float = DRIFT_SLACK, threshold: float = DRIFT_THRESHOLD) -> None:
        if slack < 0 or threshold <= 0:
            raise ValueError(f"slack must be >= 0 and threshold > 0, got {slack} and {threshold}")
        self.slack, self.threshold = slack, threshold
        self.above = self.below = 0.0  # les deux sommes : dérive vers le haut, vers le bas

    def next(self, point: Point, band: Band | None) -> Flag | None:
        if band is None:
            return None
        z = z_score(point.value, band)
        if abs(z) > band.k:
            return None
        # max(0, …) : une somme ne descend jamais sous 0
        self.above = max(0.0, self.above + z - self.slack)
        self.below = max(0.0, self.below - z - self.slack)
        if self.above <= self.threshold and self.below <= self.threshold:
            return None
        cusum = self.above if self.above > self.threshold else -self.below
        self.above = self.below = 0.0  # on repart de 0 : une dérive qui dure redéclenche plus loin
        return Flag(point.timestamp, point.value, band.expected, z, point.label, cusum, band.k)


def has_drifts(method: Method) -> bool:
    """CUSUM en saisonnier seulement : la moyenne glissante suit une dérive en 1 heure, il ne
    resterait rien à additionner (178 fausses alarmes sur 209 sur la série simulée).
    """
    return method is Method.SEASONAL


def flag_drifts(
    points: Sequence[Point],
    bands: Sequence[Band | None],
    slack: float = DRIFT_SLACK,
    threshold: float = DRIFT_THRESHOLD,
) -> list[Flag]:
    """Alarmes CUSUM sur toute une série : voir Cusum."""
    cusum = Cusum(slack, threshold)
    drifts = (cusum.next(point, band) for point, band in zip(points, bands, strict=True))
    return [drift for drift in drifts if drift is not None]
