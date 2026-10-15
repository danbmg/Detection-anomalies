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
