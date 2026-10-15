"""Command-line entry point: `python -m anomaly <command>`."""

import argparse
import csv
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from anomaly.simulate import (
    DEFAULT_START,
    Anomaly,
    Point,
    default_anomalies,
    parse_anomaly,
    simulate,
)

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


def write_csv(points: list[Point], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")  # default is \r\n, which breaks awk/grep
        writer.writerow(["timestamp", "value", "label"])
        for point in points:
            timestamp = point.timestamp.isoformat(timespec="minutes")
            writer.writerow([timestamp, point.value, point.label or ""])


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
    command.set_defaults(run=run_simulate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anomaly",
        description="Detect abnormal behavior in a time series metric without fixed thresholds.",
    )
    commands = parser.add_subparsers(dest="command", title="commands", metavar="<command>")
    add_simulate_command(commands)
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
