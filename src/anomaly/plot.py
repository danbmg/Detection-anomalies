"""Graphique PNG d'une métrique : série, bande normale, points marqués, alertes, vérité terrain."""

import math
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from matplotlib.dates import AutoDateLocator, ConciseDateFormatter, date2num
from matplotlib.figure import Figure

from anomaly.alerting import Alert
from anomaly.detect import Flag
from anomaly.model import Kind, Point
from anomaly.normal import Band

# Palette claire. Le bleu de la série et l'orange des flags passent tous les tests de daltonisme
# ensemble ; les alertes sont des événements : encre neutre et forme propre, pas de 3e teinte.
SURFACE = "#fcfcfb"
SERIES = "#2a78d6"  # la métrique et sa bande normale : la bande est la plage propre de la série
FLAG = "#eb6834"
ALERT = "#0b0b0b"
TRUTH = "#d4001a"  # vérité terrain : rouge vif, pour qu'on voie même les anomalies très courtes
TEXT = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"


def truth_spans(points: Sequence[Point]) -> list[tuple[datetime, datetime]]:
    """Séries de points étiquetés (vérité terrain), chacune finissant au point suivant."""
    spans = []
    start = None
    for point in points:
        if point.label is not None and start is None:
            start = point.timestamp
        elif point.label is None and start is not None:
            spans.append((start, point.timestamp))
            start = None
    if start is not None:
        spans.append((start, points[-1].timestamp))
    return spans


def plot_metric(
    path: Path,
    title: str,
    subtitle: str,
    points: Sequence[Point],
    bands: Sequence[Band | None],
    flags: Sequence[Flag],
    alerts: Sequence[Alert],
) -> None:
    figure = Figure(figsize=(14, 5.5), dpi=120, facecolor=SURFACE, layout="constrained")
    axes = figure.add_subplot()
    axes.set_facecolor(SURFACE)
    x = date2num([point.timestamp for point in points])

    for i, (start, end) in enumerate(truth_spans(points)):
        label = "True anomaly (ground truth)" if i == 0 else None
        axes.axvspan(date2num(start), date2num(end), color=TRUTH, alpha=0.2, lw=0, label=label)
        for edge in (
            start,
            end,
        ):  # une anomalie de 30 min fait moins d'un pixel : les bords la montrent
            axes.axvline(date2num(edge), color=TRUTH, lw=2.5, alpha=0.9, zorder=2)

    k = next((band.k for band in bands if band is not None), math.nan)
    lower = [band.lower if band else math.nan for band in bands]  # nan : en chauffe, pas de bande
    upper = [band.upper if band else math.nan for band in bands]
    band_label = f"Normal range (± {k:g} std)"
    axes.fill_between(x, lower, upper, color=SERIES, alpha=0.14, lw=0, label=band_label)
    axes.plot(x, [point.value for point in points], color=SERIES, lw=1.1, label="Value")

    axes.scatter(
        date2num([flag.timestamp for flag in flags]),
        [flag.value for flag in flags],
        s=40,
        color=FLAG,
        edgecolors=SURFACE,  # un anneau couleur du fond garde chaque point lisible sur la ligne
        linewidths=1.5,
        zorder=3,
        label="Flagged point (|z| > k)",
    )
    for alert in alerts:
        axes.axvline(date2num(alert.raised_at), color=ALERT, lw=0.8, alpha=0.5, zorder=1)
    # même encre pour toutes les alertes : une dérive se distingue par un triangle creux
    groups = [([alert for alert in alerts if alert.kind is not Kind.DRIFT], ALERT, "Alert")]
    drifts = [alert for alert in alerts if alert.kind is Kind.DRIFT]
    if drifts:  # pas de légende pour une dérive absente (méthode glissante)
        groups.append((drifts, SURFACE, "Drift alert (CUSUM)"))
    for group, face, label in groups:
        axes.plot(
            date2num([alert.raised_at for alert in group]),
            [1.0] * len(group),
            transform=axes.get_xaxis_transform(),  # x en dates, y au bord haut du graphique
            ls="",
            marker="v",
            ms=8,
            color=ALERT,
            markerfacecolor=face,
            markeredgewidth=1.5,
            clip_on=False,
            zorder=4,
            label=label,
        )

    values = [point.value for point in points]
    axes.set_ylim(
        min(0.0, min(values)), max(values) * 1.08
    )  # les bandes peuvent déborder : les données d'abord
    axes.set_xlim(x[0], x[-1])
    locator = AutoDateLocator()
    axes.xaxis.set_major_locator(locator)
    axes.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    axes.grid(axis="y", color=GRID, lw=0.8)
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(MUTED)
    axes.tick_params(colors=TEXT, labelsize=9, length=0)
    figure.suptitle(title, x=0.01, ha="left", fontsize=13, color="#0b0b0b", fontweight="bold")
    axes.set_title(
        subtitle, loc="left", fontsize=9.5, color=TEXT, pad=16
    )  # de la place pour les alertes
    axes.legend(loc="upper left", bbox_to_anchor=(0, -0.08), ncols=6, frameon=False, fontsize=9)

    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, facecolor=SURFACE)
