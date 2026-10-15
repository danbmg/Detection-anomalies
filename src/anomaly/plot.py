"""PNG chart of a metric: the series, its normal band, flagged points, alerts and ground truth."""

import math
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from matplotlib.dates import AutoDateLocator, ConciseDateFormatter, date2num
from matplotlib.figure import Figure

from anomaly.alerting import Alert
from anomaly.detect import Flag
from anomaly.model import Point
from anomaly.normal import Band

# Light chart palette. The series blue and the flag orange pass every colour-blindness check
# together; alerts are events, drawn in neutral ink with their own shape instead of a third hue.
SURFACE = "#fcfcfb"
SERIES = "#2a78d6"  # the metric and its normal band: the band is the series' own range
FLAG = "#eb6834"
ALERT = "#0b0b0b"
TRUTH = "#c3c2b7"  # ground truth: a recessive gray wash behind the data
TEXT = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"


def truth_spans(points: Sequence[Point]) -> list[tuple[datetime, datetime]]:
    """Runs of points that carry a ground-truth label, each ending where the next point starts."""
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
        axes.axvspan(date2num(start), date2num(end), color=TRUTH, alpha=0.45, lw=0, label=label)

    k = next((band.k for band in bands if band is not None), math.nan)
    lower = [band.lower if band else math.nan for band in bands]  # nan: warming up, no band
    upper = [band.upper if band else math.nan for band in bands]
    band_label = f"Normal range (± {k:g} std)"
    axes.fill_between(x, lower, upper, color=SERIES, alpha=0.14, lw=0, label=band_label)
    axes.plot(x, [point.value for point in points], color=SERIES, lw=1.1, label="Value")

    axes.scatter(
        date2num([flag.timestamp for flag in flags]),
        [flag.value for flag in flags],
        s=40,
        color=FLAG,
        edgecolors=SURFACE,  # a ring in the surface colour keeps each dot readable on the line
        linewidths=1.5,
        zorder=3,
        label="Flagged point (|z| > k)",
    )
    for alert in alerts:
        axes.axvline(date2num(alert.raised_at), color=ALERT, lw=0.8, alpha=0.5, zorder=1)
    axes.plot(
        date2num([alert.raised_at for alert in alerts]),
        [1.0] * len(alerts),
        transform=axes.get_xaxis_transform(),  # x in dates, y at the top edge of the plot
        ls="",
        marker="v",
        ms=8,
        color=ALERT,
        clip_on=False,
        zorder=4,
        label="Alert",
    )

    values = [point.value for point in points]
    axes.set_ylim(min(0.0, min(values)), max(values) * 1.08)  # bands may run off: data first
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
    axes.set_title(subtitle, loc="left", fontsize=9.5, color=TEXT, pad=16)  # room for the alerts
    axes.legend(loc="upper left", bbox_to_anchor=(0, -0.08), ncols=5, frameon=False, fontsize=9)

    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, facecolor=SURFACE)
