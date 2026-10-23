"""Plage normale d'une métrique apprise de son passé : moyenne ± k x écart-type glissants."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_WINDOW = 12  # points précédents derrière chaque bande : 1 heure à un point toutes les 5 min
DEFAULT_K = 3.0  # demi-largeur de la bande, en écarts-types


@dataclass(frozen=True)
class Band:
    expected: float  # moyenne des points précédents
    std: float  # leur écart-type : de combien la métrique s'écarte d'habitude de cette moyenne
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
    """Une bande par valeur, calculée sur les `window` valeurs précédentes, jamais les suivantes.

    Les `window` premières valeurs reçoivent None : elles « chauffent », sans assez d'historique.
    """
    if window < 2:
        raise ValueError(f"window must be at least 2 points, got {window}")
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    bands: list[Band | None] = [None] * min(window, len(values))
    for i in range(window, len(values)):
        past = values[
            i - window : i
        ]  # s'arrête avant i : une valeur ne fait jamais partie de sa propre bande
        mean = sum(past) / window
        variance = sum((x - mean) ** 2 for x in past) / window
        bands.append(Band(mean, math.sqrt(variance), k))
    return bands
