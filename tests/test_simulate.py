import csv
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.simulate import (
    BATCH_EXTRA,
    DEFAULT_SIZE,
    DEFAULT_START,
    STEP,
    Kind,
    Point,
    default_anomalies,
    make_anomaly,
    normal_level,
    parse_anomaly,
    simulate,
)

MONDAY = DEFAULT_START
SATURDAY = MONDAY + timedelta(days=5)
POINTS_PER_WEEK = 7 * 24 * 12


def at(day: datetime, hour: int, minute: int = 0) -> datetime:
    return day + timedelta(hours=hour, minutes=minute)


def labelled(points: list[Point]) -> list[Point]:
    return [point for point in points if point.label is not None]


def test_daily_cycle_peaks_in_the_afternoon() -> None:
    assert normal_level(at(MONDAY, 16)) > 3 * normal_level(at(MONDAY, 4))


def test_weekend_is_quieter_than_weekdays() -> None:
    assert normal_level(at(SATURDAY, 16)) == pytest.approx(0.7 * normal_level(at(MONDAY, 16)))


def test_nightly_batch_peak_is_above_the_daytime_peak() -> None:
    batch = normal_level(at(MONDAY, 2, 15))
    assert batch > normal_level(at(MONDAY, 16))
    assert batch - normal_level(at(MONDAY, 2, 35)) > BATCH_EXTRA * 0.9


def test_one_point_every_five_minutes() -> None:
    points = simulate(MONDAY, 1, seed=1, anomalies=[])
    assert len(points) == POINTS_PER_WEEK
    assert points[0].timestamp == MONDAY
    assert all(b.timestamp - a.timestamp == STEP for a, b in zip(points, points[1:], strict=False))


def test_same_seed_gives_the_same_series() -> None:
    anomalies = default_anomalies(MONDAY, 4)
    assert simulate(MONDAY, 4, 7, anomalies) == simulate(MONDAY, 4, 7, anomalies)
    assert simulate(MONDAY, 1, 7, []) != simulate(MONDAY, 1, 8, [])


def test_clean_series_has_no_label() -> None:
    assert labelled(simulate(MONDAY, 1, seed=1, anomalies=[])) == []


@pytest.mark.parametrize("kind", [Kind.SPIKE, Kind.DROP])
def test_spike_and_drop_multiply_the_value_only_inside_their_window(kind: Kind) -> None:
    anomaly = make_anomaly(kind, at(MONDAY, 14), timedelta(minutes=30))
    clean = simulate(MONDAY, 1, seed=3, anomalies=[])
    injected = simulate(MONDAY, 1, seed=3, anomalies=[anomaly])
    inside = labelled(injected)
    assert [point.timestamp for point in inside] == [at(MONDAY, 14, m) for m in range(0, 30, 5)]
    assert all(point.label == kind for point in inside)
    for before, after in zip(clean, injected, strict=True):
        factor = DEFAULT_SIZE[kind] if anomaly.covers(after.timestamp) else 1.0
        assert after.value == pytest.approx(before.value * factor, abs=0.01)


def test_drift_grows_slowly_up_to_its_size() -> None:
    drift = make_anomaly(Kind.DRIFT, MONDAY + timedelta(days=1), timedelta(days=2))
    clean = simulate(MONDAY, 1, seed=3, anomalies=[])
    injected = simulate(MONDAY, 1, seed=3, anomalies=[drift])
    ratios = [
        after.value / before.value
        for before, after in zip(clean, injected, strict=True)
        if after.label == Kind.DRIFT
    ]
    assert len(ratios) == 2 * 24 * 12
    assert ratios[0] == pytest.approx(1.0, rel=1e-3)
    assert ratios[-1] == pytest.approx(DEFAULT_SIZE[Kind.DRIFT], rel=1e-2)
    assert ratios == sorted(ratios)


def test_anomaly_outside_the_series_is_refused() -> None:
    late = make_anomaly(Kind.SPIKE, MONDAY + timedelta(weeks=2), timedelta(hours=1))
    with pytest.raises(ValueError, match="outside the series"):
        simulate(MONDAY, 1, seed=1, anomalies=[late])


def test_default_scenario_keeps_what_fits() -> None:
    assert [a.kind for a in default_anomalies(MONDAY, 4)] == ["spike", "spike", "drop", "drift"]
    assert [a.kind for a in default_anomalies(MONDAY, 2)] == ["spike", "spike"]


def test_parse_anomaly() -> None:
    anomaly = parse_anomaly("drift@2026-01-20T08:00/3d")
    assert anomaly.kind is Kind.DRIFT
    assert anomaly.start == datetime(2026, 1, 20, 8)
    assert anomaly.duration == timedelta(days=3)
    assert anomaly.size == DEFAULT_SIZE[Kind.DRIFT]


@pytest.mark.parametrize(
    "spec", ["spike", "boom@2026-01-20T08:00/1h", "spike@yesterday/1h", "spike@2026-01-20/0min"]
)
def test_parse_anomaly_rejects_bad_specs(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_anomaly(spec)


def test_simulate_command_writes_the_ground_truth(tmp_path: Path) -> None:
    out = tmp_path / "sub" / "traffic.csv"
    code = main(
        ["simulate", "--weeks", "1", "--anomaly", "spike@2026-01-06T14:00/1h", "--out", str(out)]
    )
    assert code == 0
    with out.open() as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == POINTS_PER_WEEK
    assert rows[0]["timestamp"] == "2026-01-05T00:00"
    assert [row["timestamp"] for row in rows if row["label"] == "spike"][0] == "2026-01-06T14:00"
    assert sum(row["label"] == "spike" for row in rows) == 12


def test_simulate_command_reports_an_anomaly_outside_the_series(tmp_path: Path) -> None:
    out = tmp_path / "traffic.csv"
    args = ["simulate", "--weeks", "1", "--anomaly", "drop@2026-03-01T10:00/1h", "--out", str(out)]
    assert main(args) == 2
    assert not out.exists()
