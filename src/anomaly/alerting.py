"""Transforme les points marqués en alertes : une par incident, seulement si l'écart dure.

Detector enchaîne tout pour un nouveau point : bande, k, écart, dérive, alertes.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from anomaly.detect import AdaptiveK, Cusum, Flag, check_point, has_drifts
from anomaly.model import Kind, Point
from anomaly.normal import DEFAULT_K, Method, band_stream

DEFAULT_CONFIRM = (
    2  # points marqués d'affilée pour ouvrir un incident : chaque anomalie simulée en donne 2
)
# Points normaux d'affilée pour le fermer : 15 minutes de calme. Avec 1 heure, un faux incident
# ouvert juste avant la chute simulée durait encore et l'avalait.
DEFAULT_QUIET = 3
# Une alarme CUSUM repart de 0 : une dérive qui dure redéclenche toutes les 2 heures environ.
# C'est la même dérive tant qu'une alarme revient dans la journée.
DRIFT_QUIET = 288


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
    def kind(self) -> Kind:
        """Ce que le détecteur a vu (pic, chute, dérive), à comparer avec la vérité terrain."""
        return self.peak.kind

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


def raise_drift_alerts(points: Sequence[Point], drifts: Sequence[Flag]) -> list[Alert]:
    """Une alerte par dérive. Une alarme CUSUM n'a pas besoin d'être confirmée : sa somme l'est."""
    return raise_alerts(points, drifts, confirm=1, quiet=DRIFT_QUIET)


class Detector:
    """Toute la chaîne, un point à la fois : sa bande, son k, l'écart, la dérive, les alertes.

    `watch` lui passe les points au fil de l'eau, `alert` toute une série d'un coup : c'est le même
    code, donc les mêmes alertes. Chaque point n'est jugé que sur ceux reçus avant lui.
    """

    def __init__(
        self,
        method: Method = Method.ROLLING,
        window: int | None = None,
        k: float = DEFAULT_K,
        adaptive: bool = False,
        confirm: int = DEFAULT_CONFIRM,
        quiet: int = DEFAULT_QUIET,
    ) -> None:
        self.next_band = band_stream(method, window, k)
        self.adaptive = AdaptiveK() if adaptive else None
        self.cusum = Cusum() if has_drifts(method) else None
        self.alerter = Alerter(confirm, quiet)
        self.drift_alerter = Alerter(1, DRIFT_QUIET)  # une alarme CUSUM est déjà confirmée

    def feed(self, point: Point) -> list[Alert]:
        """Les alertes que ce point ouvre : en général aucune, parfois un écart et une dérive."""
        band = self.next_band(point)
        if self.adaptive is not None:
            band = self.adaptive.next(point, band)
        alerts = [self.alerter.feed(check_point(point, band))]
        if self.cusum is not None:
            alerts.append(self.drift_alerter.feed(self.cusum.next(point, band)))
        return [alert for alert in alerts if alert is not None]
