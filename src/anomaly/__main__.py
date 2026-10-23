"""Point d'entrée en ligne de commande : `python -m anomaly <commande>`."""

import argparse
import os
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from anomaly.alerting import (
    DEFAULT_CONFIRM,
    DEFAULT_QUIET,
    Alert,
    Detector,
    raise_alerts,
    raise_drift_alerts,
)
from anomaly.detect import (
    DRIFT_SLACK,
    DRIFT_THRESHOLD,
    K_LIMITS,
    Flag,
    adapt_bands,
    flag_drifts,
    flag_outside,
    has_drifts,
)
from anomaly.evaluate import Score, score_alerts
from anomaly.model import Kind, Point
from anomaly.normal import (
    DEFAULT_K,
    DEFAULT_WINDOW,
    FIXED_WINDOW,
    SEASONAL_WINDOW,
    Band,
    Method,
    normal_bands,
)
from anomaly.plot import plot_metric
from anomaly.simulate import (
    DEFAULT_START,
    Anomaly,
    default_anomalies,
    parse_anomaly,
    simulate,
)
from anomaly.storage import DEFAULT_DB, History, read_csv, write_csv

DEFAULT_LOG = Path("data/alerts.log")
DEFAULT_SPEED = 86400.0  # 1 jour de données par seconde : les 4 semaines simulées en 28 secondes

# `type` garde l'alias paresseux : la forme générique n'existe que pour le vérificateur de types.
type Commands = argparse._SubParsersAction[argparse.ArgumentParser]


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def non_negative_float(text: str) -> float:
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be at least 0, got {value}")
    return value


def anomaly_spec(text: str) -> Anomaly:
    try:
        return parse_anomaly(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def add_db_option(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help=f"history database (default: {DEFAULT_DB})"
    )


def run_simulate(args: argparse.Namespace) -> int:
    anomalies: list[Anomaly]
    if args.no_anomalies:
        anomalies = []
    elif args.anomaly:
        anomalies = args.anomaly
    else:
        anomalies = default_anomalies(args.start, args.weeks)
    try:
        points = simulate(args.start, args.weeks, args.seed, anomalies)
    except ValueError as error:
        print(f"anomaly simulate: {error}", file=sys.stderr)
        return 2
    write_csv(points, args.out)
    first, last = points[0].timestamp, points[-1].timestamp
    print(f"Wrote {len(points)} points ({first} -> {last}) to {args.out}")
    for anomaly in anomalies:
        print(f"  {anomaly.kind:<5}  {anomaly.start}  for {anomaly.duration}  x{anomaly.size}")
    if args.store:
        with History(args.db) as history:
            new = history.add(args.store, points)
        print(f"Stored in metric '{args.store}' ({args.db}): {new} new points")
    return 0


def add_simulate_command(commands: Commands) -> None:
    command = commands.add_parser(
        "simulate", help="generate a web traffic metric with known anomalies (CSV)"
    )
    command.add_argument("--weeks", type=positive_int, default=4, help="length (default: 4)")
    command.add_argument("--seed", type=int, default=42, help="same seed, same series")
    command.add_argument(
        "--start",
        type=datetime.fromisoformat,
        default=DEFAULT_START,
        help=f"first timestamp (default: {DEFAULT_START.isoformat(timespec='minutes')})",
    )
    injected = command.add_mutually_exclusive_group()
    injected.add_argument(
        "--anomaly",
        type=anomaly_spec,
        action="append",
        metavar="KIND@START/DURATION",
        help="spike, drop or drift, e.g. spike@2026-01-14T14:00/30min (repeatable, "
        "replaces the default scenario)",
    )
    injected.add_argument("--no-anomalies", action="store_true", help="a clean series")
    command.add_argument(
        "--out", type=Path, default=Path("data/traffic.csv"), help="(default: data/traffic.csv)"
    )
    command.add_argument(
        "--store", metavar="METRIC", help="also store the series in the history under this name"
    )
    add_db_option(command)
    command.set_defaults(run=run_simulate)


def run_import(args: argparse.Namespace) -> int:
    try:
        points = read_csv(args.csv)
    except (OSError, ValueError) as error:
        print(f"anomaly import: {error}", file=sys.stderr)
        return 2
    with History(args.db) as history:
        new = history.add(args.metric, points)
    print(f"Read {len(points)} points from {args.csv}: {new} new in '{args.metric}' ({args.db})")
    return 0


def add_import_command(commands: Commands) -> None:
    command = commands.add_parser("import", help="store a CSV file (timestamp,value[,label])")
    command.add_argument("metric", help="name of the metric, e.g. traffic")
    command.add_argument("csv", type=Path, help="CSV file to import")
    add_db_option(command)
    command.set_defaults(run=run_import)


def add_metric_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("metric", help="name of the metric, e.g. traffic")
    command.add_argument(
        "--from", dest="start", type=datetime.fromisoformat, metavar="TIME", help="included"
    )
    command.add_argument(
        "--to", dest="end", type=datetime.fromisoformat, metavar="TIME", help="excluded"
    )
    add_db_option(command)


def add_band_options(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--method",
        type=Method,
        choices=tuple(Method),
        default=Method.ROLLING,
        help="rolling: mean of the last points; seasonal: same time on previous days of the same "
        "kind, weekday or weekend; fixed: one band learned on the first points, then frozen "
        "(default: rolling)",
    )
    command.add_argument(
        "--window",
        type=int,
        help=f"previous points behind each band (default: {DEFAULT_WINDOW}, i.e. 1 hour, "
        f"rolling; {SEASONAL_WINDOW}, i.e. 1 day, seasonal; {FIXED_WINDOW}, i.e. 1 week, fixed)",
    )
    command.add_argument(
        "--k", type=float, default=DEFAULT_K, help=f"band half-width in std (default: {DEFAULT_K})"
    )
    low, high = K_LIMITS
    command.add_argument(
        "--adaptive",
        action="store_true",
        help=f"recompute k every hour from the last day's z-scores: up when the recent noise is "
        f"higher than the band expects, down when lower, between {low:g} and {high:g}",
    )


def compute_bands(points: list[Point], args: argparse.Namespace) -> list[Band | None]:
    bands = normal_bands(points, args.method, args.window, args.k)
    return adapt_bands(points, bands) if args.adaptive else bands


def adaptive_note(args: argparse.Namespace) -> str:
    return " adaptive" if args.adaptive else ""


def load_metric(args: argparse.Namespace) -> list[Point] | None:
    """Points de la métrique jusqu'à --to, depuis le premier : une bande a besoin des précédents."""
    error = f"anomaly {args.command}:"
    if not args.db.exists():
        print(f"{error} no history at {args.db}, import a metric first", file=sys.stderr)
        return None
    with History(args.db) as history:
        known = history.metrics()
        points = history.read(args.metric, end=args.end)
    if args.metric not in known:
        names = ", ".join(known) or "none"
        print(f"{error} unknown metric '{args.metric}' (known: {names})", file=sys.stderr)
        return None
    return points


def is_shown(timestamp: datetime, args: argparse.Namespace) -> bool:
    return args.start is None or timestamp >= args.start


def run_show(args: argparse.Namespace) -> int:
    points = load_metric(args)
    if points is None:
        return 2
    bands: list[Band | None] = [None] * len(points)
    if args.band:
        try:
            bands = compute_bands(points, args)
        except ValueError as error:
            print(f"anomaly show: {error}", file=sys.stderr)
            return 2
    for point, band in zip(points, bands, strict=True):
        if not is_shown(point.timestamp, args):
            continue
        line = f"{point.timestamp.isoformat(timespec='minutes')}  {point.value:>8.2f}"
        if args.band:
            line += f"  {describe(band)}"
        print(f"{line}  {point.label or ''}".rstrip())
    return 0


def describe(band: Band | None) -> str:
    if band is None:
        return f"{'warming up':<36}"
    return f"expected {band.expected:>8.2f}  [{band.lower:>8.2f}, {band.upper:>8.2f}]"


def add_show_command(commands: Commands) -> None:
    command = commands.add_parser("show", help="print the stored points of a metric, oldest first")
    add_metric_options(command)
    command.add_argument("--band", action="store_true", help="also print the normal range")
    add_band_options(command)
    command.set_defaults(run=run_show)


def run_detect(args: argparse.Namespace) -> int:
    points = load_metric(args)
    if points is None:
        return 2
    try:
        bands = compute_bands(points, args)
        flags = flag_outside(points, bands)
        drifts = find_drifts(points, bands, args.method)
    except ValueError as error:
        print(f"anomaly detect: {error}", file=sys.stderr)
        return 2
    flags = [flag for flag in flags if is_shown(flag.timestamp, args)]
    drifts = [flag for flag in drifts if is_shown(flag.timestamp, args)]
    for flag in sorted([*flags, *drifts], key=lambda flag: flag.timestamp):
        timestamp = flag.timestamp.isoformat(timespec="minutes")
        line = f"{timestamp}  {flag.value:>8.2f}  expected {flag.expected:>8.2f}  {score(flag)}"
        print(f"{line}  {flag.label or ''}".rstrip())
    judged = [point for point in points if is_shown(point.timestamp, args)]
    known = any(point.label for point in judged)  # vérité terrain connue : combien sont justes
    settings = f"{args.method}, window {args.window}, k {args.k}{adaptive_note(args)}"
    summary = f"{len(flags)} flagged points out of {len(judged)} ({settings})"
    if known:
        summary += f", {sum(flag.label is not None for flag in flags)} on a true anomaly"
    print(summary)
    if args.method is Method.SEASONAL:
        settings = f"CUSUM, slack {DRIFT_SLACK}, threshold {DRIFT_THRESHOLD}"
        summary = f"{len(drifts)} drift alarms ({settings})"
        if known:
            summary += f", {sum(flag.label is not None for flag in drifts)} on a true anomaly"
        print(summary)
    return 0


def find_drifts(points: list[Point], bands: list[Band | None], method: Method) -> list[Flag]:
    return flag_drifts(points, bands) if has_drifts(method) else []


def score(flag: Flag) -> str:
    """Le z-score d'un écart, ou la somme CUSUM d'une dérive."""
    return f"z {flag.z:>+8.2f}" if flag.cusum is None else f"cusum {flag.cusum:>+8.2f}"


def add_detect_command(commands: Commands) -> None:
    command = commands.add_parser("detect", help="print the points outside their normal range")
    add_metric_options(command)
    add_band_options(command)
    command.set_defaults(run=run_detect)


def run_alert(args: argparse.Namespace) -> int:
    points = load_metric(args)
    if points is None:
        return 2
    try:
        detector = make_detector(args)
    except ValueError as error:
        print(f"anomaly alert: {error}", file=sys.stderr)
        return 2
    # Le lot rejoue la série dans le même Detector que `watch` : mêmes points, mêmes alertes.
    alerts = [alert for point in points for alert in detector.feed(point)]
    alerts = [alert for alert in alerts if is_shown(alert.raised_at, args)]
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("a") as log:
        for alert in alerts:
            line = describe_alert(args.metric, alert)
            print(line)
            log.write(line + "\n")
    k = f"k {args.k}{adaptive_note(args)}"
    settings = f"{args.method}, {k}, confirm {args.confirm}, quiet {args.quiet}"
    summary = f"{len(alerts)} alerts ({settings}), logged to {args.log}"
    if any(point.label for point in points if is_shown(point.timestamp, args)):
        summary += f", {sum(alert.label is not None for alert in alerts)} on a true anomaly"
    print(summary)
    return 0


def make_detector(args: argparse.Namespace) -> Detector:
    return Detector(args.method, args.window, args.k, args.adaptive, args.confirm, args.quiet)


def describe_alert(metric: str, alert: Alert) -> str:
    peak = alert.peak
    when = alert.raised_at.isoformat(timespec="minutes")
    since = alert.start.isoformat(timespec="minutes")
    line = (
        f"{when}  ALERT {metric}  {alert.kind:<5}  since {since}  value {peak.value:.2f}"
        f"  expected {peak.expected:.2f}  {score(peak)}  k {peak.k:.2f}"
    )
    return f"{line}  {alert.label or ''}".rstrip()


def add_alert_options(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--confirm",
        type=positive_int,
        default=DEFAULT_CONFIRM,
        help=f"flagged points in a row to raise an alert (default: {DEFAULT_CONFIRM})",
    )
    command.add_argument(
        "--quiet",
        type=positive_int,
        default=DEFAULT_QUIET,
        help=f"normal points in a row that end an incident (default: {DEFAULT_QUIET})",
    )


def add_alert_command(commands: Commands) -> None:
    command = commands.add_parser("alert", help="raise one alert per lasting deviation (incident)")
    add_metric_options(command)
    add_band_options(command)
    add_alert_options(command)
    add_log_option(command)
    command.set_defaults(run=run_alert)


def add_log_option(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--log", type=Path, default=DEFAULT_LOG, help=f"alerts are appended here ({DEFAULT_LOG})"
    )


def run_plot(args: argparse.Namespace) -> int:
    points = load_metric(args)
    if points is None:
        return 2
    try:
        bands = compute_bands(points, args)
        flags = flag_outside(points, bands)
        alerts = raise_alerts(points, flags, args.confirm, args.quiet)
        alerts += raise_drift_alerts(points, find_drifts(points, bands, args.method))
    except ValueError as error:
        print(f"anomaly plot: {error}", file=sys.stderr)
        return 2
    shown = [i for i, point in enumerate(points) if is_shown(point.timestamp, args)]
    if not shown:
        print("anomaly plot: no point in this time range", file=sys.stderr)
        return 2
    points, bands = points[shown[0] :], bands[shown[0] :]
    flags = [flag for flag in flags if is_shown(flag.timestamp, args)]
    alerts = [alert for alert in alerts if is_shown(alert.raised_at, args)]
    first, last = (points[i].timestamp.isoformat(timespec="minutes") for i in (0, -1))
    subtitle = (
        f"{first} → {last}   ·   {args.method}, window {args.window} points, "
        f"k {args.k:g}{adaptive_note(args)}, "
        f"confirm {args.confirm}, quiet {args.quiet}   ·   "
        f"{len(flags)} flagged points, {len(alerts)} alerts"
    )
    out = args.out or Path(f"data/{args.metric}.png")
    title = f"{args.metric}: normal range and anomalies"
    plot_metric(out, title, subtitle, points, bands, flags, alerts)
    print(f"Wrote {out} ({len(points)} points, {len(flags)} flagged, {len(alerts)} alerts)")
    return 0


def add_plot_command(commands: Commands) -> None:
    command = commands.add_parser("plot", help="draw the metric, its normal range and anomalies")
    add_metric_options(command)
    add_band_options(command)
    add_alert_options(command)
    command.add_argument("--out", type=Path, help="PNG file (default: data/<metric>.png)")
    command.set_defaults(run=run_plot)


def run_watch(args: argparse.Namespace) -> int:
    try:
        arriving = sorted(read_csv(args.csv), key=lambda point: point.timestamp)
        detector = make_detector(args)
    except (OSError, ValueError) as error:
        print(f"anomaly watch: {error}", file=sys.stderr)
        return 2
    args.log.parent.mkdir(parents=True, exist_ok=True)
    stored = raised = 0
    with History(args.db) as history, args.log.open("a") as log:
        known = history.read(args.metric)
        for point in known:  # le passé déjà stocké règle la bande ; ses alertes sont du passé
            detector.feed(point)
        last = known[-1].timestamp if known else None
        arriving = [point for point in arriving if last is None or point.timestamp > last]
        print(
            f"Watching '{args.metric}': {len(known)} points in history, {len(arriving)} new "
            f"points in {args.csv}, replayed at x{args.speed:g} (Ctrl+C to stop)",
            flush=True,
        )
        started = time.monotonic()
        try:
            for point in arriving:
                if args.speed > 0:
                    # Chaque point attend son heure d'arrivée (accélérée) depuis le début : le
                    # temps de calcul ne s'ajoute pas aux attentes, le retard ne s'accumule pas.
                    elapsed = (point.timestamp - arriving[0].timestamp).total_seconds()
                    time.sleep(max(0.0, started + elapsed / args.speed - time.monotonic()))
                if last is None or point.timestamp.date() != last.date():
                    day = point.timestamp.date().isoformat()
                    print(f"{day}  {stored} new points stored, {raised} alerts", flush=True)
                last = point.timestamp
                history.add(args.metric, [point])  # stocké dès son arrivée, avant d'être jugé
                stored += 1
                for alert in detector.feed(point):
                    line = describe_alert(args.metric, alert)
                    # flush : l'alerte s'affiche tout de suite, même à travers un pipe (`| tee`)
                    print(line, flush=True)
                    log.write(line + "\n")
                    log.flush()
                    raised += 1
        except KeyboardInterrupt:
            print("Stopped by the user")
    print(f"{stored} new points stored, {raised} alerts, logged to {args.log}")
    return 0


def add_watch_command(commands: Commands) -> None:
    command = commands.add_parser(
        "watch", help="replay a CSV point by point: store, detect and alert as points arrive"
    )
    command.add_argument("metric", help="name of the metric, e.g. traffic")
    command.add_argument(
        "csv", type=Path, help="CSV whose points arrive one by one (timestamp,value[,label])"
    )
    command.add_argument(
        "--speed",
        type=non_negative_float,
        default=DEFAULT_SPEED,
        help=f"times faster than real time (default: {DEFAULT_SPEED:g}, i.e. 1 day of data "
        "per second; 0: no waiting)",
    )
    add_db_option(command)
    add_band_options(command)
    add_alert_options(command)
    add_log_option(command)
    command.set_defaults(run=run_watch)


# Méthodes comparées par `evaluate`, du seuil fixe (la référence du sujet) à la plus complète.
EVALUATED = (
    ("fixed threshold", Method.FIXED, False),
    ("rolling", Method.ROLLING, False),
    ("rolling, adaptive k", Method.ROLLING, True),
    ("seasonal", Method.SEASONAL, False),
    ("seasonal, adaptive k", Method.SEASONAL, True),
)


def run_evaluate(args: argparse.Namespace) -> int:
    anomalies = default_anomalies(DEFAULT_START, 4)
    scores = {name: Score() for name, _, _ in EVALUATED}
    for seed in range(args.seed, args.seed + args.runs):
        points = simulate(DEFAULT_START, 4, seed, anomalies)
        for name, method, adaptive in EVALUATED:
            try:
                detector = Detector(method, None, args.k, adaptive, args.confirm, args.quiet)
            except ValueError as error:
                print(f"anomaly evaluate: {error}", file=sys.stderr)
                return 2
            alerts = [alert for point in points for alert in detector.feed(point)]
            scores[name] += score_alerts(points, alerts)

    last = args.seed + args.runs - 1
    seeds = f"seed {args.seed}" if args.runs == 1 else f"seeds {args.seed} to {last}"
    kinds = ", ".join(anomaly.kind for anomaly in anomalies)
    print(f"Simulated traffic, 4 weeks, {seeds}: {len(anomalies)} true anomalies each ({kinds})")
    print(f"k {args.k}, confirm {args.confirm}, quiet {args.quiet}\n")
    header = ("method", "alerts", "false", "missed", "precision", "recall", "delay", "missed kinds")
    print(
        f"{header[0]:<22}{header[1]:>7}{header[2]:>7}{header[3]:>7}{header[4]:>11}"
        f"{header[5]:>8}{header[6]:>9}  {header[7]}"
    )
    for name, result in scores.items():
        print(
            f"{name:<22}{result.alerts:>7}{result.false:>7}{len(result.missed):>7}"
            f"{percent(result.precision):>11}{percent(result.recall):>8}"
            f"{hours(result.mean_delay):>9}  {count_kinds(result.missed)}".rstrip()
        )
    print(
        "\nfalse      alerts raised outside every true anomaly (false positives)\n"
        "missed     true anomalies without any alert (false negatives)\n"
        "precision  share of the alerts that are correct\n"
        "recall     share of the true anomalies that got an alert\n"
        "delay      mean time from the start of a true anomaly to its first alert"
    )
    return 0


def percent(share: float | None) -> str:
    return "-" if share is None else f"{share:.0%}"


def hours(delay: timedelta | None) -> str:
    return "-" if delay is None else f"{delay / timedelta(hours=1):.1f} h"


def count_kinds(kinds: tuple[Kind, ...]) -> str:
    return ", ".join(kind if n == 1 else f"{kind} x{n}" for kind, n in Counter(kinds).items())


def add_evaluate_command(commands: Commands) -> None:
    command = commands.add_parser(
        "evaluate", help="compare every method with the ground truth of simulated series"
    )
    command.add_argument("--seed", type=int, default=42, help="first series (default: 42)")
    command.add_argument(
        "--runs", type=positive_int, default=1, help="series to add up, from --seed (default: 1)"
    )
    command.add_argument(
        "--k", type=float, default=DEFAULT_K, help=f"band half-width in std (default: {DEFAULT_K})"
    )
    add_alert_options(command)
    command.set_defaults(run=run_evaluate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anomaly",
        description="Detect abnormal behavior in a time series metric without fixed thresholds.",
    )
    commands = parser.add_subparsers(dest="command", title="commands", metavar="<command>")
    add_simulate_command(commands)
    add_import_command(commands)
    add_show_command(commands)
    add_detect_command(commands)
    add_alert_command(commands)
    add_plot_command(commands)
    add_watch_command(commands)
    add_evaluate_command(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    if "method" in args and args.window is None:  # la fenêtre par défaut dépend de la méthode
        args.window = args.method.default_window
    run: Callable[[argparse.Namespace], int] = args.run
    try:
        return run(args)
    except BrokenPipeError:
        # Le lecteur a fermé le pipe (`| head`) : on s'arrête sans bruit. Rediriger stdout vers
        # devnull évite que le vidage final de Python ne relève l'erreur une seconde fois.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
