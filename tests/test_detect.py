import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.detect import Flag, flag_deviations, z_score
from anomaly.model import Kind, Point
from anomaly.normal import Band
from anomaly.simulate import DEFAULT_START, default_anomalies, simulate
from anomaly.storage import History

T0 = datetime(2026, 1, 5)


def points_of(values: list[float], label: Kind | None = None) -> list[Point]:
    return [Point(T0 + i * timedelta(minutes=5), v, label) for i, v in enumerate(values)]


def test_z_score_counts_standard_deviations() -> None:
    band = Band(expected=10, std=2, k=3)
    assert z_score(16, band) == 3
    assert z_score(5, band) == -2.5
    assert z_score(10, band) == 0


def test_z_score_on_a_flat_history() -> None:
    flat = Band(expected=5, std=0, k=3)
    assert z_score(5, flat) == 0
    assert z_score(6, flat) == math.inf
    assert z_score(4, flat) == -math.inf


def test_point_outside_the_band_is_flagged_with_its_details() -> None:
    # The 6 points before the last one: mean 11, std 1, so 30 is 19 standard deviations away.
    points = points_of([10, 12, 10, 12, 10, 12, 30], Kind.SPIKE)
    expected = Flag(points[6].timestamp, value=30, expected=11, z=19, label=Kind.SPIKE)
    assert flag_deviations(points, window=6, k=3) == [expected]


def test_exactly_k_standard_deviations_is_still_normal() -> None:
    assert flag_deviations(points_of([10, 12, 10, 12, 10, 12, 14]), window=6, k=3) == []
    assert len(flag_deviations(points_of([10, 12, 10, 12, 10, 12, 14.1]), window=6, k=3)) == 1


def test_low_values_are_flagged_too() -> None:
    flags = flag_deviations(points_of([10, 12, 10, 12, 10, 12, 1]), window=6, k=3)
    assert [flag.z for flag in flags] == [-10]


def test_flat_series_neither_crashes_nor_flags() -> None:
    assert flag_deviations(points_of([5.0] * 50), window=12, k=3) == []


def test_change_after_a_flat_series_is_flagged() -> None:
    flags = flag_deviations(points_of([5.0] * 20 + [6.0]), window=12, k=3)
    assert [(flag.value, flag.z) for flag in flags] == [(6.0, math.inf)]


def test_warming_up_points_are_never_flagged() -> None:
    assert flag_deviations(points_of([1000, 1, 1000, 1, 10, 10]), window=4, k=3) == []


def test_every_simulated_anomaly_is_flagged() -> None:
    anomalies = default_anomalies(DEFAULT_START, 4)
    flags = flag_deviations(simulate(DEFAULT_START, 4, 42, anomalies))
    for anomaly in anomalies:
        assert any(anomaly.covers(flag.timestamp) for flag in flags), anomaly


def test_detect_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "h.db")
    with History(tmp_path / "h.db") as history:
        history.add("m", points_of([10, 12, 10, 12, 10, 12, 30, 11]))
    assert main(["detect", "m", "--window", "6", "--db", db]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["2026-01-05T00:30", "30.00", "expected", "11.00", "z", "+19.00"]
    assert lines[1] == "1 flagged points out of 8 (window 6, k 3.0)"

    assert main(["detect", "m", "--window", "6", "--from", "2026-01-05T00:35", "--db", db]) == 0
    assert capsys.readouterr().out.splitlines() == ["0 flagged points out of 1 (window 6, k 3.0)"]

    assert main(["detect", "nope", "--db", db]) == 2
    assert "unknown metric 'nope' (known: m)" in capsys.readouterr().err


def test_detect_command_counts_flags_on_true_anomalies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "h.db")
    out = str(tmp_path / "t.csv")
    assert main(["simulate", "--out", out, "--store", "traffic", "--db", db]) == 0
    capsys.readouterr()
    assert main(["detect", "traffic", "--db", db]) == 0
    assert capsys.readouterr().out.splitlines()[-1].endswith("on a true anomaly")
