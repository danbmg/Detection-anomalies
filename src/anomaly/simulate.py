"""Synthetic web app traffic (requests per minute) with anomalies injected at known times."""

import math
import random
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from anomaly.model import Kind, Point

STEP = timedelta(minutes=5)
DEFAULT_START = datetime(2026, 1, 5)  # a Monday at midnight

BASE_LEVEL = 200.0  # mean requests per minute on a weekday
DAILY_AMPLITUDE = 0.6  # traffic swings from 40% (04:00) to 160% (16:00) of the base level
WEEKEND_FACTOR = 0.7
BATCH_START, BATCH_END = time(2, 0), time(2, 30)
BATCH_EXTRA = 300.0  # added by the nightly batch job: higher than the daytime peak, yet normal
NOISE = 0.05  # standard deviation of the noise, as a fraction of the level


DEFAULT_SIZE = {Kind.SPIKE: 3.0, Kind.DROP: 0.2, Kind.DRIFT: 1.5}


@dataclass(frozen=True)
class Anomaly:
    kind: Kind
    start: datetime
    duration: timedelta
    size: float  # multiplier: x3 for a spike, x0.2 for a drop, x1.5 at the end of a drift

    @property
    def end(self) -> datetime:
        return self.start + self.duration

    def covers(self, t: datetime) -> bool:
        return self.start <= t < self.end

    def factor(self, t: datetime) -> float:
        """Multiplier applied to the normal value at time t, 1.0 outside the anomaly."""
        if not self.covers(t):
            return 1.0
        if self.kind is Kind.DRIFT:
            progress = (t - self.start) / self.duration  # grows from 0 to 1 across the drift
            return 1.0 + (self.size - 1.0) * progress
        return self.size


def make_anomaly(kind: Kind, start: datetime, duration: timedelta) -> Anomaly:
    return Anomaly(kind, start, duration, DEFAULT_SIZE[kind])


def normal_level(t: datetime) -> float:
    """Expected traffic at time t, without noise or anomalies."""
    hour = t.hour + t.minute / 60
    daily = 1.0 - DAILY_AMPLITUDE * math.cos(2 * math.pi * (hour - 4) / 24)
    weekly = WEEKEND_FACTOR if t.weekday() >= 5 else 1.0
    level = BASE_LEVEL * daily * weekly
    if BATCH_START <= t.time() < BATCH_END:
        level += BATCH_EXTRA
    return level


def simulate(start: datetime, weeks: int, seed: int, anomalies: list[Anomaly]) -> list[Point]:
    """One point every 5 minutes over `weeks` weeks. The same seed always gives the same series."""
    end = start + timedelta(weeks=weeks)
    for anomaly in anomalies:
        if not start <= anomaly.start < end:
            raise ValueError(f"{anomaly.kind} at {anomaly.start} is outside the series")
    rng = random.Random(seed)
    points = []
    t = start
    while t < end:
        level = normal_level(t)
        # One random draw per point, so injecting an anomaly never changes the noise elsewhere.
        value = max(0.0, rng.gauss(level, NOISE * level))
        label = None
        for anomaly in anomalies:  # if anomalies overlap, the last one gives the label
            if anomaly.covers(t):
                value *= anomaly.factor(t)
                label = anomaly.kind
        points.append(Point(t, round(value, 2), label))
        t += STEP
    return points


def default_anomalies(start: datetime, weeks: int) -> list[Anomaly]:
    """Built-in scenario after a quiet first week. A series shorter than 4 weeks keeps what fits."""
    day, hour, minute = timedelta(days=1), timedelta(hours=1), timedelta(minutes=1)
    scenario = [
        make_anomaly(Kind.SPIKE, start + 9 * day + 14 * hour, 30 * minute),
        # At night x3 stays below the normal daytime peak: invisible to a fixed threshold.
        make_anomaly(Kind.SPIKE, start + 12 * day + 3.5 * hour, 20 * minute),
        make_anomaly(Kind.DROP, start + 16 * day + 10 * hour, 2 * hour),
        make_anomaly(Kind.DRIFT, start + 22 * day, 4 * day),
    ]
    end = start + timedelta(weeks=weeks)
    return [anomaly for anomaly in scenario if anomaly.end <= end]


SPEC = re.compile(r"(?P<kind>\w+)@(?P<start>[^/]+)/(?P<amount>\d+)(?P<unit>min|h|d)")
UNITS = {"min": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}


def parse_anomaly(spec: str) -> Anomaly:
    """Read `KIND@START/DURATION`, e.g. `spike@2026-01-14T14:00/30min`."""
    match = SPEC.fullmatch(spec)
    if match is None:
        example = "spike@2026-01-14T14:00/30min"
        raise ValueError(f"expected KIND@START/DURATION (e.g. {example}), got {spec!r}")
    if match["kind"] not in tuple(Kind):
        raise ValueError(f"unknown anomaly kind {match['kind']!r}, expected spike, drop or drift")
    duration = int(match["amount"]) * UNITS[match["unit"]]
    if duration <= timedelta(0):
        raise ValueError(f"duration must be positive, got {spec!r}")
    return make_anomaly(Kind(match["kind"]), datetime.fromisoformat(match["start"]), duration)
