"""Command-line entry point: `python -m anomaly <command>`."""

import argparse
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from anomaly.normal import DEFAULT_K, DEFAULT_WINDOW, Band, rolling_bands
from anomaly.simulate import (
    DEFAULT_START,
    Anomaly,
    default_anomalies,
    parse_anomaly,
    simulate,
)
from anomaly.storage import DEFAULT_DB, History, read_csv, write_csv

# `type` keeps the alias lazy: the generic form only exists for the type checker.
type Commands = argparse._SubParsersAction[argparse.ArgumentParser]


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
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


def run_show(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"anomaly show: no history at {args.db}, import a metric first", file=sys.stderr)
        return 2
    with History(args.db) as history:
        known = history.metrics()
        # The band of the first point shown needs the points before it: read from the beginning.
        points = history.read(args.metric, None if args.band else args.start, args.end)
    if args.metric not in known:
        names = ", ".join(known) or "none"
        print(f"anomaly show: unknown metric '{args.metric}' (known: {names})", file=sys.stderr)
        return 2
    bands: list[Band | None] = [None] * len(points)
    if args.band:
        try:
            bands = rolling_bands([point.value for point in points], args.window, args.k)
        except ValueError as error:
            print(f"anomaly show: {error}", file=sys.stderr)
            return 2
    for point, band in zip(points, bands, strict=True):
        if args.start and point.timestamp < args.start:
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
    command.add_argument("metric", help="name of the metric, e.g. traffic")
    command.add_argument(
        "--from", dest="start", type=datetime.fromisoformat, metavar="TIME", help="included"
    )
    command.add_argument(
        "--to", dest="end", type=datetime.fromisoformat, metavar="TIME", help="excluded"
    )
    command.add_argument("--band", action="store_true", help="also print the normal range")
    command.add_argument(
        "--window",
        type=int,
        default=DEFAULT_WINDOW,
        help=f"previous points behind each band (default: {DEFAULT_WINDOW}, i.e. 1 hour)",
    )
    command.add_argument(
        "--k", type=float, default=DEFAULT_K, help=f"band half-width in std (default: {DEFAULT_K})"
    )
    add_db_option(command)
    command.set_defaults(run=run_show)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anomaly",
        description="Detect abnormal behavior in a time series metric without fixed thresholds.",
    )
    commands = parser.add_subparsers(dest="command", title="commands", metavar="<command>")
    add_simulate_command(commands)
    add_import_command(commands)
    add_show_command(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    run: Callable[[argparse.Namespace], int] = args.run
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
