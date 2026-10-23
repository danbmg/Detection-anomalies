"""Plage normale d'une métrique apprise de son passé : glissante (cycle 1) ou saisonnière (#8)."""

import math
import statistics
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from anomaly.model import Point

DEFAULT_WINDOW = 12  # points précédents derrière chaque bande : 1 heure à un point toutes les 5 min
# Écarts précédents derrière l'écart habituel saisonnier : 1 jour. Saison retirée, l'écart en %
# est le même à toute heure. Sur 1 heure, trop peu de points : l'estimation variait trop.
SEASONAL_WINDOW = 288
DEFAULT_K = 3.0  # demi-largeur de la bande, en écarts-types
DEFAULT_DAYS = 5  # jours de référence derrière une valeur attendue saisonnière : une semaine ouvrée
DAY = timedelta(days=1)


class Method(StrEnum):
    ROLLING = "rolling"  # moyenne de la dernière heure : simple, mais prend le batch pour un pic
    SEASONAL = "seasonal"  # même heure les jours précédents : le batch de 2h devient attendu

    @property
    def default_window(self) -> int:
        return SEASONAL_WINDOW if self is Method.SEASONAL else DEFAULT_WINDOW


@dataclass(frozen=True)
class Band:
    expected: float  # valeur attendue : moyenne glissante ou médiane saisonnière
    std: float  # de combien la métrique s'écarte d'habitude de cette valeur
    k: float

    @property
    def lower(self) -> float:
        return self.expected - self.k * self.std

    @property
    def upper(self) -> float:
        return self.expected + self.k * self.std


def check_settings(window: int, k: float) -> None:
    if window < 2:
        raise ValueError(f"window must be at least 2 points, got {window}")
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")


def rolling_bands(
    values: Sequence[float], window: int = DEFAULT_WINDOW, k: float = DEFAULT_K
) -> list[Band | None]:
    """Une bande par valeur, calculée sur les `window` valeurs précédentes, jamais les suivantes.

    Les `window` premières valeurs reçoivent None : elles « chauffent », sans assez d'historique.
    """
    check_settings(window, k)
    bands: list[Band | None] = [None] * min(window, len(values))
    for i in range(window, len(values)):
        # s'arrête avant i : une valeur ne fait jamais partie de sa propre bande
        past = values[i - window : i]
        mean = sum(past) / window
        variance = sum((x - mean) ** 2 for x in past) / window
        bands.append(Band(mean, math.sqrt(variance), k))
    return bands


def is_weekend(t: datetime) -> bool:
    return t.weekday() >= 5


def same_time_before(
    value_at: dict[datetime, float], t: datetime, first: datetime, days: int
) -> list[float]:
    """Valeurs à la même heure, les `days` jours précédents du même type (ouvré ou week-end)."""
    found: list[float] = []
    before = t - DAY
    while before >= first and len(found) < days:
        if is_weekend(before) == is_weekend(t) and before in value_at:
            found.append(value_at[before])
        before -= DAY
    return found


def seasonal_bands(
    points: Sequence[Point],
    days: int = DEFAULT_DAYS,
    window: int = SEASONAL_WINDOW,
    k: float = DEFAULT_K,
) -> list[Band | None]:
    """Une bande par point : la médiane de la même heure les `days` jours précédents du même type,
    ± k fois l'écart habituel, mesuré en % de l'attendu sur les `window` points précédents.

    Un point chauffe tant qu'il n'a aucun jour de référence ou pas encore `window` écarts derrière.
    """
    check_settings(window, k)
    if days < 1:
        raise ValueError(f"days must be at least 1, got {days}")
    if not points:
        return []
    value_at = {point.timestamp: point.value for point in points}
    # Le bruit grandit avec le trafic : 20 req/min d'écart, c'est énorme à 4h et rien à 16h.
    # On compte donc les écarts en % de l'attendu, et seuls les `window` derniers sont gardés.
    gaps: deque[float] = deque(maxlen=window)
    bands: list[Band | None] = []
    for point in points:
        same_time = same_time_before(value_at, point.timestamp, points[0].timestamp, days)
        if not same_time:
            bands.append(None)
            continue
        # La médiane ignore un jour de référence anormal, là où une moyenne recopierait son pic.
        expected = statistics.median(same_time)
        band = None
        if len(gaps) == window:
            # écart-type autour de 0 : la médiane est déjà l'attendu, l'écart moyen n'en fait pas
            # partie. Le recentrer ferait suivre l'anomalie à la bande, comme une moyenne glissante.
            spread = math.sqrt(sum(gap * gap for gap in gaps) / window)
            band = Band(expected, abs(expected) * spread, k)
        bands.append(band)
        # Un point hors de sa bande n'apprend rien sur l'écart habituel : sinon un pic de 30 min
        # élargissait la bande pendant tout un jour, et le détecteur devenait presque aveugle.
        outside = band is not None and not band.lower <= point.value <= band.upper
        if expected != 0 and not outside:  # un écart en % d'un attendu nul n'existe pas
            gaps.append((point.value - expected) / abs(expected))
    return bands


def normal_bands(
    points: Sequence[Point],
    method: Method = Method.ROLLING,
    window: int | None = None,
    k: float = DEFAULT_K,
) -> list[Band | None]:
    """Une bande par point, avec la méthode choisie. Sans `window`, celle de la méthode."""
    if window is None:
        window = method.default_window
    if method is Method.SEASONAL:
        return seasonal_bands(points, DEFAULT_DAYS, window, k)
    return rolling_bands([point.value for point in points], window, k)
