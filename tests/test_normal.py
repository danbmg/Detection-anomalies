import random
import statistics
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.model import Point
from anomaly.normal import Band, rolling_bands, seasonal_bands
from anomaly.storage import History

MONDAY = datetime(2026, 1, 5)


def daily(values: Sequence[float], hour: int = 12) -> list[Point]:
    """One point per day at the same hour, from a Monday."""
    return [Point(MONDAY + timedelta(days=i, hours=hour), v, None) for i, v in enumerate(values)]


def test_band_from_a_hand_computed_window() -> None:
    # Mean of 2, 4, 4, 4, 5, 5, 7, 9 is 5; squared gaps 9+1+1+1+0+0+4+16 = 32; 32/8 = 4; sqrt = 2.
    bands = rolling_bands([2, 4, 4, 4, 5, 5, 7, 9, 100], window=8, k=3)
    assert bands[8] == Band(expected=5, std=2, k=3)
    assert (bands[8].lower, bands[8].upper) == (-1, 11)


def test_band_slides_with_the_window() -> None:
    # Point 2 is judged on [1, 3] (mean 2, std 1), point 3 on [3, 5] (mean 4, std 1).
    bands = rolling_bands([1, 3, 5, 100], window=2, k=2)
    assert bands[2:] == [Band(2, 1, 2), Band(4, 1, 2)]
    assert bands[3] is not None
    assert (bands[3].lower, bands[3].upper) == (2, 6)


def test_first_points_are_warming_up() -> None:
    bands = rolling_bands([1.0] * 10, window=4, k=3)
    assert len(bands) == 10
    assert bands[:4] == [None] * 4
    assert all(band is not None for band in bands[4:])
    assert rolling_bands([1.0, 2.0], window=4, k=3) == [None, None]


def test_a_band_never_looks_at_its_own_point_or_later() -> None:
    values = [10.0, 12.0, 11.0, 13.0, 12.0, 11.0, 10.0, 12.0]
    before = rolling_bands(values, window=3, k=3)
    changed = values[:5] + [1000.0] + values[6:]  # only point 5 changes
    after = rolling_bands(changed, window=3, k=3)
    assert after[:6] == before[:6]  # bands up to point 5 included are untouched
    assert after[6] != before[6]  # point 6 is the first one to see it


def test_flat_series_has_a_zero_width_band() -> None:
    bands = rolling_bands([7.0] * 5, window=3, k=3)
    assert bands[3] == Band(7.0, 0.0, 3)


def test_matches_the_statistics_module_on_random_data() -> None:
    rng = random.Random(0)
    values = [rng.gauss(100, 15) for _ in range(200)]
    for i, band in enumerate(rolling_bands(values, window=12, k=3)):
        if band is None:
            continue
        past = values[i - 12 : i]
        assert band.expected == pytest.approx(statistics.fmean(past))
        assert band.std == pytest.approx(statistics.pstdev(past))


@pytest.mark.parametrize(("window", "k"), [(1, 3.0), (0, 3.0), (12, 0.0), (12, -1.0)])
def test_rejects_bad_settings(window: int, k: float) -> None:
    with pytest.raises(ValueError):
        rolling_bands([1.0] * 20, window=window, k=k)


def test_seasonal_expected_is_the_median_of_the_same_time_on_previous_days() -> None:
    # Midnight and noon, Monday to Friday. Friday noon is judged on Thursday, Wednesday and
    # Tuesday noon: the median is 100, where a mean would copy Tuesday's 300 (166.7).
    midnight = daily([10, 10, 10, 10, 10], hour=0)
    noon = daily([100, 300, 100, 100, 50])
    points = sorted(midnight + noon, key=lambda point: point.timestamp)
    bands = seasonal_bands(points, days=3, window=2, k=3)
    assert bands[-1] is not None
    assert bands[-1].expected == 100


def test_weekend_days_are_only_compared_with_weekend_days() -> None:
    # Two weeks, 100 on weekdays and 70 on weekends. Monday has no previous day and Tuesday,
    # Wednesday only 0 and 1 gap behind them: they warm up. The first Saturday warms up too,
    # because five previous weekdays say nothing about a weekend.
    week = [100, 100, 100, 100, 100, 70, 70]
    bands = seasonal_bands(daily(week * 2), days=5, window=2, k=3)
    expected = [band.expected if band else None for band in bands]
    assert expected == [None, None, None, 100, 100, None, 70, 100, 100, 100, 100, 100, 70, 70]


def test_seasonal_spread_is_a_percentage_of_the_expected_value() -> None:
    # Tuesday is 10 % above Monday, Wednesday 10 % below Tuesday: the usual gap is 10 %.
    # Thursday expects Wednesday's 99, so its band is 99 ± 2 x 9.9.
    bands = seasonal_bands(daily([100, 110, 99, 0]), days=1, window=2, k=2)
    assert bands[3] is not None
    assert bands[3].expected == 99
    assert bands[3].std == pytest.approx(9.9)
    # Ten times less traffic, same 10 % gaps: a band ten times narrower.
    small = seasonal_bands(daily([10, 11, 9.9, 0]), days=1, window=2, k=2)[3]
    assert small is not None
    assert small.std == pytest.approx(0.99)


def test_a_seasonal_band_never_looks_at_its_own_point_or_later() -> None:
    rng = random.Random(0)
    hourly = [Point(MONDAY + timedelta(hours=i), rng.gauss(100, 10), None) for i in range(24 * 6)]
    before = seasonal_bands(hourly, days=3, window=12, k=3)
    i = 24 * 4 + 5  # a point once bands exist
    # Only point i changes, a little: it stays inside its band, so its gap is learned.
    changed = [*hourly[:i], Point(hourly[i].timestamp, hourly[i].value + 1, None), *hourly[i + 1 :]]
    after = seasonal_bands(changed, days=3, window=12, k=3)
    assert before[i] is not None
    assert after[: i + 1] == before[: i + 1]
    assert after[i + 1] != before[i + 1]  # the next point is the first to see its gap


def test_a_point_outside_its_band_does_not_widen_the_next_bands() -> None:
    # Learning the usual gap from a spike would make spikes look usual for a whole window.
    rng = random.Random(0)
    hourly = [Point(MONDAY + timedelta(hours=i), rng.gauss(100, 5), None) for i in range(24 * 6)]
    i = 24 * 4 + 5
    spiked = [*hourly[:i], Point(hourly[i].timestamp, 1000.0, None), *hourly[i + 1 :]]
    bands = seasonal_bands(spiked, days=3, window=12, k=3)
    spike, after = bands[i], bands[i + 1]
    assert spike is not None and after is not None
    assert not spike.lower <= 1000 <= spike.upper
    assert after.std / after.expected == pytest.approx(spike.std / spike.expected)


def test_flat_series_has_a_zero_width_seasonal_band() -> None:
    assert seasonal_bands(daily([7.0] * 5), days=3, window=2, k=3)[4] == Band(7.0, 0.0, 3)
    # Expected 0: a gap in % of 0 does not exist, so no band, but no crash either.
    assert seasonal_bands(daily([0.0] * 5), days=3, window=2, k=3) == [None] * 5
    assert seasonal_bands([], days=3, window=2, k=3) == []


@pytest.mark.parametrize(("days", "window", "k"), [(0, 12, 3.0), (5, 1, 3.0), (5, 12, 0.0)])
def test_seasonal_rejects_bad_settings(days: int, window: int, k: float) -> None:
    with pytest.raises(ValueError):
        seasonal_bands(daily([1.0] * 20), days=days, window=window, k=k)


def test_show_band_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "h.db"
    t0 = datetime(2026, 1, 5)
    values = [10.0, 12.0, 10.0, 12.0, 50.0]
    points = [Point(t0 + i * timedelta(minutes=5), v, None) for i, v in enumerate(values)]
    with History(db) as history:
        history.add("m", points)

    args = ["show", "m", "--band", "--window", "4", "--k", "2", "--db", str(db)]
    assert main([*args, "--from", "2026-01-05T00:15"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("2026-01-05T00:15") and "warming up" in lines[0]
    assert lines[1].split() == [
        "2026-01-05T00:20",
        "50.00",
        "expected",
        "11.00",
        "[",
        "9.00,",
        "13.00]",
    ]

    assert main(["show", "m", "--band", "--window", "1", "--db", str(db)]) == 2
    assert "window must be at least 2" in capsys.readouterr().err


def test_show_band_command_with_the_seasonal_method(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with History(tmp_path / "h.db") as history:
        history.add("m", daily([100, 300, 100, 120]))
    db = str(tmp_path / "h.db")
    args = ["show", "m", "--band", "--method", "seasonal", "--window", "2", "--db", db]
    assert main(args) == 0
    # Thursday expects the median of Monday to Wednesday noon: 100, not 300.
    lines = capsys.readouterr().out.splitlines()
    assert "warming up" in lines[2]
    assert lines[3].split()[:4] == ["2026-01-08T12:00", "120.00", "expected", "100.00"]

    with pytest.raises(SystemExit) as exit_info:  # argparse refuses an unknown method
        main(["show", "m", "--band", "--method", "weekly", "--db", db])
    assert exit_info.value.code == 2
