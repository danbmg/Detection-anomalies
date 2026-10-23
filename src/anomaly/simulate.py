"""Trafic synthétique d'une application web (requêtes/min), anomalies à des dates connues."""

import math
import random
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from anomaly.model import Kind, Point

STEP = timedelta(minutes=5)
DEFAULT_START = datetime(2026, 1, 5)  # un lundi à minuit

BASE_LEVEL = 200.0  # requêtes par minute en moyenne un jour de semaine
DAILY_AMPLITUDE = 0.6  # le trafic varie de 40 % (4h) à 160 % (16h) du niveau de base
WEEKEND_FACTOR = 0.7
BATCH_START, BATCH_END = time(2, 0), time(2, 30)
BATCH_EXTRA = 300.0  # ajouté par le batch de nuit : plus haut que le pic de jour, pourtant normal
NOISE = 0.05  # écart-type du bruit, en fraction du niveau


DEFAULT_SIZE = {Kind.SPIKE: 3.0, Kind.DROP: 0.2, Kind.DRIFT: 1.5}


@dataclass(frozen=True)
class Anomaly:
    kind: Kind
    start: datetime
    duration: timedelta
    size: float  # multiplicateur : x3 pour un pic, x0,2 pour une chute, x1,5 à la fin d'une dérive

    @property
    def end(self) -> datetime:
        return self.start + self.duration

    def covers(self, t: datetime) -> bool:
        return self.start <= t < self.end

    def factor(self, t: datetime) -> float:
        """Multiplicateur appliqué à la valeur normale à l'instant t, 1.0 hors de l'anomalie."""
        if not self.covers(t):
            return 1.0
        if self.kind is Kind.DRIFT:
            progress = (t - self.start) / self.duration  # va de 0 à 1 pendant la dérive
            return 1.0 + (self.size - 1.0) * progress
        return self.size


def make_anomaly(kind: Kind, start: datetime, duration: timedelta) -> Anomaly:
    return Anomaly(kind, start, duration, DEFAULT_SIZE[kind])


def normal_level(t: datetime) -> float:
    """Trafic attendu à l'instant t, sans bruit ni anomalie."""
    hour = t.hour + t.minute / 60
    daily = 1.0 - DAILY_AMPLITUDE * math.cos(2 * math.pi * (hour - 4) / 24)
    weekly = WEEKEND_FACTOR if t.weekday() >= 5 else 1.0
    level = BASE_LEVEL * daily * weekly
    if BATCH_START <= t.time() < BATCH_END:
        level += BATCH_EXTRA
    return level


def simulate(start: datetime, weeks: int, seed: int, anomalies: list[Anomaly]) -> list[Point]:
    """Un point toutes les 5 minutes sur `weeks` semaines. Même graine, même série."""
    end = start + timedelta(weeks=weeks)
    for anomaly in anomalies:
        if not start <= anomaly.start < end:
            raise ValueError(f"{anomaly.kind} at {anomaly.start} is outside the series")
    rng = random.Random(seed)
    points = []
    t = start
    while t < end:
        level = normal_level(t)
        # Un tirage aléatoire par point : injecter une anomalie ne change jamais le bruit ailleurs.
        value = max(0.0, rng.gauss(level, NOISE * level))
        label = None
        for anomaly in anomalies:  # si des anomalies se chevauchent, la dernière donne l'étiquette
            if anomaly.covers(t):
                value *= anomaly.factor(t)
                label = anomaly.kind
        points.append(Point(t, round(value, 2), label))
        t += STEP
    return points


def default_anomalies(start: datetime, weeks: int) -> list[Anomaly]:
    """Scénario intégré après une semaine calme. Une série plus courte garde ce qui rentre."""
    day, hour, minute = timedelta(days=1), timedelta(hours=1), timedelta(minutes=1)
    scenario = [
        make_anomaly(Kind.SPIKE, start + 9 * day + 14 * hour, 30 * minute),
        # La nuit, x3 reste sous le pic normal de la journée : invisible pour un seuil fixe.
        make_anomaly(Kind.SPIKE, start + 12 * day + 3.5 * hour, 20 * minute),
        make_anomaly(Kind.DROP, start + 16 * day + 10 * hour, 2 * hour),
        make_anomaly(Kind.DRIFT, start + 22 * day, 4 * day),
    ]
    end = start + timedelta(weeks=weeks)
    return [anomaly for anomaly in scenario if anomaly.end <= end]


SPEC = re.compile(r"(?P<kind>\w+)@(?P<start>[^/]+)/(?P<amount>\d+)(?P<unit>min|h|d)")
UNITS = {"min": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}


def parse_anomaly(spec: str) -> Anomaly:
    """Lit `TYPE@DÉBUT/DURÉE`, par exemple `spike@2026-01-14T14:00/30min`."""
    match = SPEC.fullmatch(spec)
    if match is None:
        example = "spike@2026-01-14T14:00/30min"
        raise ValueError(f"expected KIND@START/DURATION (e.g. {example}), got {spec!r}")
    if match["kind"] not in DEFAULT_SIZE:  # « anomaly » n'existe que dans les données réelles
        raise ValueError(f"unknown anomaly kind {match['kind']!r}, expected spike, drop or drift")
    duration = int(match["amount"]) * UNITS[match["unit"]]
    if duration <= timedelta(0):
        raise ValueError(f"duration must be positive, got {spec!r}")
    return make_anomaly(Kind(match["kind"]), datetime.fromisoformat(match["start"]), duration)
