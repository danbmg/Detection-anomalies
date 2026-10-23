from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.model import Kind, Point
from anomaly.plot import truth_spans
from anomaly.storage import History

T0 = datetime(2026, 1, 5)


def at(i: int) -> datetime:
    return T0 + i * timedelta(minutes=5)


def test_truth_spans_group_labelled_points() -> None:
    labels = [None, Kind.SPIKE, Kind.SPIKE, None, None, Kind.DROP]
    points = [Point(at(i), 1.0, label) for i, label in enumerate(labels)]
    assert truth_spans(points) == [(at(1), at(3)), (at(5), at(5))]
    assert truth_spans([Point(at(0), 1.0, None)]) == []


@pytest.fixture
def db(tmp_path: Path) -> Path:
    values = [10, 12, 10, 12, 10, 12, 30, 100, 11, 30, 11, 12]
    labels = [None] * 6 + [Kind.SPIKE, Kind.SPIKE] + [None] * 4
    rows = enumerate(zip(values, labels, strict=True))
    points = [Point(at(i), value, label) for i, (value, label) in rows]
    with History(tmp_path / "h.db") as history:
        history.add("m", points)
    return tmp_path / "h.db"


def test_plot_command_writes_a_png(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "charts" / "m.png"
    assert main(["plot", "m", "--window", "6", "--db", str(db), "--out", str(out)]) == 0
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert capsys.readouterr().out == f"Wrote {out} (12 points, 2 flagged, 1 alerts)\n"


def test_plot_command_draws_only_the_chosen_range(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "m.png"
    args = ["plot", "m", "--window", "6", "--db", str(db), "--out", str(out)]
    assert main([*args, "--from", "2026-01-05T00:35", "--to", "2026-01-05T00:50"]) == 0
    assert "(3 points, 1 flagged, 1 alerts)" in capsys.readouterr().out

    assert main([*args, "--from", "2026-02-01"]) == 2
    assert "no point in this time range" in capsys.readouterr().err


def test_plot_command_defaults_to_the_data_folder(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["plot", "m", "--window", "6", "--db", str(db)]) == 0
    assert (tmp_path / "data" / "m.png").exists()


def test_plot_command_draws_drift_alerts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, out = str(tmp_path / "h.db"), tmp_path / "t.png"
    assert main(["simulate", "--out", str(tmp_path / "t.csv"), "--store", "t", "--db", db]) == 0
    capsys.readouterr()
    assert main(["plot", "t", "--method", "seasonal", "--db", db, "--out", str(out)]) == 0
    assert "59 flagged, 4 alerts" in capsys.readouterr().out  # 2 spikes, the drop and the drift
