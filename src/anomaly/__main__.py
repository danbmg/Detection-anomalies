"""Command-line entry point: `python -m anomaly <command>`."""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anomaly",
        description="Detect abnormal behavior in a time series metric without fixed thresholds.",
    )
    # Each issue adds its own command here (simulate, import, detect, plot...).
    parser.add_subparsers(dest="command", title="commands", metavar="<command>")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
