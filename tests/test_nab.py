import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.model import Kind, Point
from anomaly.simulate import parse_anomaly
from anomaly.storage import History, label_windows, read_windows

T0 = datetime(2026, 1, 5)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


@pytest.fixture
def nab(tmp_path: Path) -> Path:
    """A small copy of the NAB layout: a CSV without labels and the windows of every file."""
    rows = [f"{at(5 * i):%Y-%m-%d %H:%M:%S},{100 + i % 2}" for i in range(12)]  # NAB's format
    lines = ["timestamp,value", *rows]
    (tmp_path / "taxi.csv").write_text("\n".join(lines) + "\n")
    windows = {
        "realKnownCause/taxi.csv": [["2026-01-05 00:10:00.000000", "2026-01-05 00:20:00.000000"]],
        "realKnownCause/other.csv": [["2020-01-01 00:00:00.000000", "2020-01-02 00:00:00.000000"]],
    }
    (tmp_path / "windows.json").write_text(json.dumps(windows))
    return tmp_path


def test_read_windows_finds_the_file_by_name(nab: Path) -> None:
    assert read_windows(nab / "windows.json", "taxi.csv") == [(at(10), at(20))]
    with pytest.raises(ValueError, match="no anomaly windows for missing.csv"):
        read_windows(nab / "windows.json", "missing.csv")


def test_points_inside_a_window_are_labelled_anomaly() -> None:
    points = [Point(at(5 * i), 1.0, None) for i in range(6)]
    labelled = label_windows(points, [(at(10), at(20))])
    assert [point.label for point in labelled] == [None, None] + [Kind.ANOMALY] * 3 + [None]


def test_import_with_nab_labels(
    nab: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "h.db"
    args = ["import", "taxi", str(nab / "taxi.csv"), "--labels", str(nab / "windows.json")]
    assert main([*args, "--db", str(db)]) == 0
    assert "Labelled 3 points 'anomaly' in 1 windows" in capsys.readouterr().out
    with History(db) as history:
        labels = [point.label for point in history.read("taxi")]
    assert labels.count(Kind.ANOMALY) == 3

    assert main([*args[:-1], str(nab / "missing.json"), "--db", str(db)]) == 2
    assert "missing.json" in capsys.readouterr().err


def test_evaluate_a_stored_metric(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The simulated series of seed 42, stored with its labels, gives the same table as evaluate.
    db = str(tmp_path / "h.db")
    assert main(["simulate", "--out", str(tmp_path / "t.csv"), "--store", "t", "--db", db]) == 0
    capsys.readouterr()
    assert main(["evaluate", "--metric", "t", "--db", db]) == 0
    stored = capsys.readouterr().out.splitlines()
    assert stored[0] == "Metric 't': 8064 points, 4 true anomalies"
    assert main(["evaluate"]) == 0
    assert capsys.readouterr().out.splitlines()[1:] == stored[1:]


def test_evaluate_needs_a_labelled_metric(
    nab: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "h.db")
    assert main(["import", "taxi", str(nab / "taxi.csv"), "--db", db]) == 0  # no --labels
    capsys.readouterr()
    assert main(["evaluate", "--metric", "taxi", "--db", db]) == 2
    assert "'taxi' has no ground truth labels" in capsys.readouterr().err
    assert main(["evaluate", "--metric", "nope", "--db", db]) == 2
    assert "unknown metric 'nope'" in capsys.readouterr().err


def test_anomaly_is_a_label_of_real_data_only() -> None:
    with pytest.raises(ValueError, match="unknown anomaly kind 'anomaly'"):
        parse_anomaly("anomaly@2026-01-14T14:00/30min")
