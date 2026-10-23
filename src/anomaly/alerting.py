"""Transforme les points marqués en alertes : une par incident, seulement si l'écart dure."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from anomaly.detect import Flag
from anomaly.model import Kind, Point

DEFAULT_CONFIRM = (
    2  # points marqués d'affilée pour ouvrir un incident : chaque anomalie simulée en donne 2
)
# Points normaux d'affilée pour le fermer : 15 minutes de calme. Avec 1 heure, un faux incident
# ouvert juste avant la chute simulée durait encore et l'avalait.
DEFAULT_QUIET = 3


@dataclass(frozen=True)
class Alert:
    flags: tuple[Flag, ...]  # les points marqués d'affilée qui ont confirmé l'incident

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
    """Nourri un point à la fois, du plus ancien au plus récent : sert au mode live comme au lot."""

    def __init__(self, confirm: int = DEFAULT_CONFIRM, quiet: int = DEFAULT_QUIET) -> None:
        if confirm < 1 or quiet < 1:
            raise ValueError(f"confirm and quiet must be at least 1, got {confirm} and {quiet}")
        self.confirm = confirm
        self.quiet = quiet
        self.run: list[
            Flag
        ] = []  # points marqués d'affilée, pas encore assez pour lever une alerte
        self.in_incident = False
        self.calm = 0  # points normaux d'affilée depuis le dernier point marqué de l'incident

    def feed(self, flag: Flag | None) -> Alert | None:
        """`flag` vaut None si le point est normal. Renvoie une alerte quand un incident s'ouvre."""
        if self.in_incident:  # un incident en cours n'alerte plus : seule sa fin est surveillée
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
    """Rejoue toute une série dans un Alerter, du plus ancien point au plus récent."""
    flag_at = {flag.timestamp: flag for flag in flags}
    alerter = Alerter(confirm, quiet)
    alerts = []
    for point in points:
        alert = alerter.feed(flag_at.get(point.timestamp))
        if alert is not None:
            alerts.append(alert)
    return alerts
