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

### Store and read the history

```bash
python -m anomaly simulate --store traffic          # also stores the series as metric "traffic"
python -m anomaly import traffic data/traffic.csv   # or import any CSV with timestamp,value[,label] columns
python -m anomaly show traffic --from 2026-01-14T13:50 --to 2026-01-14T14:40
```

The history is a SQLite file, `data/history.db` by default (`--db` to change it), with one row per metric and timestamp:
importing the same points again replaces them instead of duplicating them.
`--from` is included, `--to` is excluded, and points always come back oldest first.

### See the normal range

```bash
python -m anomaly show traffic --band --from 2026-01-14T13:45 --to 2026-01-14T14:45
python -m anomaly show traffic --band --window 24 --k 2.5   # 2 hours of history, narrower band
```

For each point, the expected value is the mean of the `--window` points just before it (default 12, i.e. 1 hour),
and the normal range is expected ± `--k` standard deviations of those points (default 3).
A point is never part of its own range, and the first `--window` points are shown as `warming up`.

### Seasonal method

```bash
python -m anomaly show traffic --band --method seasonal --from 2026-01-14T01:50 --to 2026-01-14T02:40
python -m anomaly alert traffic --method seasonal   # 4 alerts, all on a true anomaly (rolling: 43)
```

The rolling mean only knows the last hour, so it takes the nightly batch for an anomaly every night.
With `--method seasonal`, the expected value is the median of the same time of day on the 5 previous days of the same kind
(weekdays are compared with weekdays, weekends with weekends): the batch at 02:00 becomes expected.
A median ignores one abnormal reference day, where a mean would copy its spike into the next days.

The band is expected ± `--k` times the usual gap, measured in % of the expected value, because noise grows with traffic.
That usual gap is learned from the `--window` previous points (default 288, i.e. 1 day, for this method),
skipping points outside their band: otherwise a spike would widen the band for a whole day.
A point warms up until it has one previous day of the same kind and `--window` gaps behind it.

### Slow drifts

A slow drift never leaves the band, so no z-score sees it, but it pulls every point to the same side.
The seasonal method therefore also runs CUSUM (cumulative sum): two running sums, one per side,
add each point's z-score minus a slack of 0.5, never going below 0.
Noise falls on both sides and cancels out; a drift keeps adding up until a sum passes 15, which raises a drift alarm.
The sum then starts again from 0. A lasting drift alarms again every few hours, but stays one alert (one drift incident)
as long as an alarm comes back within a day.
Points outside their band are left to the z-score, so a spike or a drop is never taken for a drift.

The simulated drift (+50 % over 4 days) raises its alert about 10 hours after it starts.
The rolling method has no CUSUM: its expected value follows a drift within the hour, leaving nothing to add up.

### Detect deviations

```bash
python -m anomaly detect traffic                                     # every flagged point + a summary
python -m anomaly detect traffic --from 2026-01-13 --to 2026-01-14   # one day only
python -m anomaly detect traffic --method seasonal                   # see "Seasonal method"
```

A point is flagged when its z-score, its distance to the expected value in standard deviations, is above `--k` (default 3).
Each line shows the value, the expected value, the z-score and the ground truth label when known;
the summary says how many flags fall on a true anomaly.
With `--method seasonal`, drift alarms are listed too, with their CUSUM sum instead of a z-score.
On a perfectly flat history a point equal to it is never flagged, and any change gets an infinite z-score.

### Raise alerts

```bash
python -m anomaly alert traffic                  # prints the alerts and appends them to data/alerts.log
python -m anomaly alert traffic --confirm 3      # require 3 flagged points in a row
```

A single flagged point is noise: an alert fires only after `--confirm` flagged points in a row (default 2).
That incident then stays open, without alerting again, until `--quiet` normal points in a row (default 3, i.e. 15 minutes).
Each run appends its alerts to the log (`--log` to change it).
Each alert says what the detector saw: `spike` (above the band), `drop` (below it) or `drift` (CUSUM);
the last column is the ground truth, when known.

### Plot

```bash
python -m anomaly plot traffic                                               # whole history -> data/traffic.png
python -m anomaly plot traffic --from 2026-01-14T08:00 --to 2026-01-14T20:00 --out spike.png
```

The chart shows the value (blue line), its normal range (blue wash), flagged points (orange dots),
alerts (black triangles at the top, hollow for a drift) and, when known, the true anomalies (red bars).
It accepts the same `--method`, `--window`, `--k`, `--confirm` and `--quiet` settings as the other commands.

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
