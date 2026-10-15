import random
import statistics
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.model import Point
from anomaly.normal import Band, rolling_bands
from anomaly.storage import History


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
