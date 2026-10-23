"""Alertes face à la vérité terrain : fausses alertes, anomalies ratées, précision, rappel."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from anomaly.alerting import Alert
from anomaly.model import Kind, Point


@dataclass(frozen=True)
class Window:
    """Une vraie anomalie : des points étiquetés d'affilée, du premier au dernier inclus."""

    kind: Kind
    start: datetime
    end: datetime

    def contains(self, t: datetime) -> bool:
        return self.start <= t <= self.end


def true_windows(points: Sequence[Point]) -> list[Window]:
    """Les vraies anomalies de la série : chaque suite de points d'affilée de même étiquette."""
    windows: list[Window] = []
    previous: Point | None = None
    for point in points:
        if point.label is not None:
            if previous is not None and previous.label is point.label:
                windows[-1] = replace(windows[-1], end=point.timestamp)
            else:
                windows.append(Window(point.label, point.timestamp, point.timestamp))
        previous = point
    return windows


@dataclass(frozen=True)
class Score:
    alerts: int = 0
    false: int = 0  # faux positifs : alertes levées hors de toute vraie anomalie
    anomalies: int = 0
    missed: tuple[Kind, ...] = ()  # faux négatifs : vraies anomalies sans aucune alerte dedans
    delays: tuple[timedelta, ...] = ()  # du début de chaque anomalie trouvée à sa 1re alerte

    @property
    def precision(self) -> float | None:
        """Part des alertes qui sont justes : 1 - précision, c'est la part de fausses alertes."""
        return (self.alerts - self.false) / self.alerts if self.alerts else None

    @property
    def recall(self) -> float | None:
        """Part des vraies anomalies qui ont reçu au moins une alerte."""
        return (self.anomalies - len(self.missed)) / self.anomalies if self.anomalies else None

    @property
    def mean_delay(self) -> timedelta | None:
        return sum(self.delays, timedelta()) / len(self.delays) if self.delays else None

    def __add__(self, other: "Score") -> "Score":
        """Additionne les scores de plusieurs séries, comme si c'était une seule."""
        return Score(
            self.alerts + other.alerts,
            self.false + other.false,
            self.anomalies + other.anomalies,
            self.missed + other.missed,
            self.delays + other.delays,
        )


def score_alerts(points: Sequence[Point], alerts: Sequence[Alert]) -> Score:
    """Une alerte est juste quand elle est levée pendant une vraie anomalie.

    La précision compte des alertes, le rappel des anomalies : trois alertes dans la même anomalie
    sont trois alertes justes, mais une seule anomalie trouvée.
    """
    windows = true_windows(points)
    false = sum(not any(window.contains(alert.raised_at) for window in windows) for alert in alerts)
    missed, delays = [], []
    for window in windows:
        inside = [alert.raised_at for alert in alerts if window.contains(alert.raised_at)]
        if inside:
            delays.append(min(inside) - window.start)
        else:
            missed.append(window.kind)
    return Score(len(alerts), false, len(windows), tuple(missed), tuple(delays))
