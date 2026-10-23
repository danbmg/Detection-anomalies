import math
import statistics
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.detect import Flag, flag_deviations, flag_drifts, z_score
from anomaly.model import Kind, Point
from anomaly.normal import Band, Method, is_weekend, normal_bands
from anomaly.simulate import (
    BATCH_END,
    BATCH_START,
    DEFAULT_START,
    default_anomalies,
    normal_level,
    simulate,
)
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


def errors(method: Method, keep: Callable[[datetime], bool]) -> list[float]:
    """How far the expected value is from the true noiseless level, in %, on a clean series."""
    points = simulate(DEFAULT_START, 4, 42, [])
    return [
        abs(band.expected - normal_level(point.timestamp)) / normal_level(point.timestamp)
        for point, band in zip(points, normal_bands(points, method), strict=True)
        if band is not None and keep(point.timestamp)
    ]


def test_seasonal_expected_follows_the_day_and_the_week() -> None:
    def everywhere(t: datetime) -> bool:
        return True

    def in_batch(t: datetime) -> bool:
        return BATCH_START <= t.time() < BATCH_END

    # About 2 % off on average, weekends included (rolling: 14 %). During the nightly batch,
    # at most 7 % off, where the rolling mean of the hour before misses it by 80 %.
    assert statistics.fmean(errors(Method.SEASONAL, everywhere)) < 0.05
    assert statistics.fmean(errors(Method.SEASONAL, is_weekend)) < 0.05
    assert max(errors(Method.SEASONAL, in_batch)) < 0.1
    assert max(errors(Method.ROLLING, in_batch)) > 0.5


def test_detect_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "h.db")
    with History(tmp_path / "h.db") as history:
        history.add("m", points_of([10, 12, 10, 12, 10, 12, 30, 11]))
    assert main(["detect", "m", "--window", "6", "--db", db]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["2026-01-05T00:30", "30.00", "expected", "11.00", "z", "+19.00"]
    assert lines[1] == "1 flagged points out of 8 (rolling, window 6, k 3.0)"

    assert main(["detect", "m", "--window", "6", "--from", "2026-01-05T00:35", "--db", db]) == 0
    summary = "0 flagged points out of 1 (rolling, window 6, k 3.0)"
    assert capsys.readouterr().out.splitlines() == [summary]

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


def test_detect_command_with_the_seasonal_method(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "h.db")
    out = str(tmp_path / "t.csv")
    assert main(["simulate", "--out", out, "--store", "traffic", "--db", db]) == 0
    capsys.readouterr()
    assert main(["detect", "traffic", "--method", "seasonal", "--db", db]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "(seasonal, window 288, k 3.0)" in lines[-2]
    assert lines[-1].startswith("44 drift alarms (CUSUM, slack 0.5, threshold 15.0)")
    assert lines[-1].endswith("44 on a true anomaly")
    drift_lines = [line.split() for line in lines if "cusum" in line]
    assert len(drift_lines) == 44
    assert drift_lines[0][4] == "cusum" and drift_lines[0][-1] == "drift"


BAND = Band(expected=100, std=10, k=3)


def drifts_of(values: list[float], slack: float = 0.5, threshold: float = 2) -> list[Flag]:
    """CUSUM on a constant band 100 ± 3 x 10: a value of 110 is z = +1."""
    return flag_drifts(points_of(values), [BAND] * len(values), slack, threshold)


def test_cusum_adds_up_small_gaps_on_the_same_side() -> None:
    # Each 110 is z = +1, so the upper sum grows by 1 - 0.5: 0.5, 1, 1.5, 2, then 2.5 > 2.
    # No single point ever leaves its band (|z| = 1 < 3), yet the 5th one raises an alarm.
    assert drifts_of([110] * 5) == [Flag(points_of([0] * 5)[4].timestamp, 110, 100, 1, None, 2.5)]


def test_cusum_starts_again_from_zero_after_an_alarm() -> None:
    flags = drifts_of([110] * 10)
    assert [flag.cusum for flag in flags] == [2.5, 2.5]  # points 4 and 9: 5 more to get there


def test_cusum_lets_noise_cancel_out() -> None:
    assert drifts_of([110, 90] * 50) == []  # +1, -1, +1…: neither sum ever reaches 2


def test_cusum_sees_downward_drifts_too() -> None:
    flags = drifts_of([90] * 5)
    assert [(flag.cusum, flag.kind) for flag in flags] == [(-2.5, Kind.DRIFT)]


def test_cusum_leaves_points_outside_their_band_to_the_z_score() -> None:
    # 150 is z = +5: a spike for flag_deviations, not 5 points' worth of drift.
    assert drifts_of([150] * 10) == []
    values = [110.0] * 5
    assert flag_drifts(points_of(values), [None] * 5, 0.5, 2) == []  # warming up: no band


def test_flag_kind_says_what_the_detector_saw() -> None:
    t = points_of([0])[0].timestamp
    assert Flag(t, 150, 100, 5, None).kind is Kind.SPIKE
    assert Flag(t, 50, 100, -5, None).kind is Kind.DROP
    assert Flag(t, 110, 100, 1, None, cusum=15.5).kind is Kind.DRIFT


@pytest.mark.parametrize(("slack", "threshold"), [(-0.5, 15.0), (0.5, 0.0)])
def test_cusum_rejects_bad_settings(slack: float, threshold: float) -> None:
    with pytest.raises(ValueError):
        drifts_of([110] * 5, slack, threshold)
