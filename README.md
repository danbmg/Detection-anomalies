# Détection d'anomalies

Detect abnormal behavior in a time series metric without fixed thresholds:
the "normal" range is learned from the metric's own history, and only significant deviations raise an alert.

## Requirements

- Python 3.12 or newer
- `make`

## Install

```bash
make install                # creates .venv/ and installs the project with its dev tools
source .venv/bin/activate   # optional: lets you type `python` instead of `.venv/bin/python`
```

## Run

```bash
python -m anomaly --help    # lists the available commands
```

### Simulate a metric

```bash
python -m anomaly simulate                  # 4 weeks of traffic + default anomalies -> data/traffic.csv
python -m anomaly simulate --no-anomalies   # a clean series
python -m anomaly simulate --weeks 2 --seed 7 \
    --anomaly spike@2026-01-08T14:00/30min --anomaly drift@2026-01-12T00:00/2d
```

The metric is a web app's traffic in requests per minute, one point every 5 minutes, with a day/night cycle,
quieter weekends and a nightly batch peak at 02:00 (normal, although higher than the daytime peak).
The CSV has three columns: `timestamp`, `value` and `label`, the ground truth (`spike`, `drop`, `drift`, or empty when normal).
The same seed always gives the same file.

## Development

| Command | Purpose |
|---|---|
| `make check` | lint, type check and tests, stops at the first failure |
| `make lint` | ruff: style errors and formatting |
| `make format` | ruff: fix what can be fixed automatically |
| `make typecheck` | mypy in strict mode |
| `make test` | pytest |
| `make fclean` | remove the virtualenv and every cache |

Code lives in `src/anomaly/`, tests in `tests/`.
