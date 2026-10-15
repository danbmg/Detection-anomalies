"""Turn flagged points into alerts: one alert per incident, only when the deviation lasts."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from anomaly.detect import Flag
from anomaly.model import Kind, Point

DEFAULT_CONFIRM = 2  # flagged points in a row to open an incident: every simulated anomaly gives 2
# Unflagged points in a row to close it: 15 minutes of calm. With 1 hour, a false incident
# opened just before the simulated drop was still running and swallowed it.
DEFAULT_QUIET = 3


@dataclass(frozen=True)
class Alert:
    flags: tuple[Flag, ...]  # the flagged points in a row that confirmed the incident

    @property
    def start(self) -> datetime:
        return self.flags[0].timestamp

    @property
    def raised_at(self) -> datetime:
        return self.flags[-1].timestamp

    @property
    def peak(self) -> Flag:
        return max(self.flags, key=lambda flag: abs(flag.z))

    @property
    def label(self) -> Kind | None:
        return next((flag.label for flag in self.flags if flag.label is not None), None)


class Alerter:
    """Fed one point at a time, oldest first, so the live mode can use it as well as a batch run."""

    def __init__(self, confirm: int = DEFAULT_CONFIRM, quiet: int = DEFAULT_QUIET) -> None:
        if confirm < 1 or quiet < 1:
            raise ValueError(f"confirm and quiet must be at least 1, got {confirm} and {quiet}")
        self.confirm = confirm
        self.quiet = quiet
        self.run: list[Flag] = []  # flagged points in a row, not enough yet to raise an alert
        self.in_incident = False
        self.calm = 0  # unflagged points in a row since the incident's last flagged point

    def feed(self, flag: Flag | None) -> Alert | None:
        """`flag` is None when the point is normal. Returns an alert when an incident opens."""
        if self.in_incident:  # an ongoing incident never alerts again: only its end is watched
            self.calm = 0 if flag else self.calm + 1
            if self.calm >= self.quiet:
                self.in_incident = False
            return None
        if flag is None:
            self.run = []
            return None
        self.run.append(flag)
        if len(self.run) < self.confirm:
            return None
        alert = Alert(tuple(self.run))
        self.run, self.in_incident, self.calm = [], True, 0
        return alert


def raise_alerts(
    points: Sequence[Point],
    flags: Sequence[Flag],
    confirm: int = DEFAULT_CONFIRM,
    quiet: int = DEFAULT_QUIET,
) -> list[Alert]:
    """Replay a whole series through an Alerter, oldest point first."""
    flag_at = {flag.timestamp: flag for flag in flags}
    alerter = Alerter(confirm, quiet)
    alerts = []
    for point in points:
        alert = alerter.feed(flag_at.get(point.timestamp))
        if alert is not None:
            alerts.append(alert)
    return alerts
