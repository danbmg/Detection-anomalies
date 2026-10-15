from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.model import Kind, Point
from anomaly.storage import History, read_csv, write_csv

T0 = datetime(2026, 1, 5)


def series(count: int, label: Kind | None = None) -> list[Point]:
    return [Point(T0 + i * timedelta(minutes=5), 100.0 + i, label) for i in range(count)]


def test_points_are_read_back_oldest_first(tmp_path: Path) -> None:
    points = series(5)
    with History(tmp_path / "h.db") as history:
        history.add("traffic", reversed(points))
        assert history.read("traffic") == points


def test_history_survives_a_reopen(tmp_path: Path) -> None:
    with History(tmp_path / "h.db") as history:
        history.add("traffic", series(3, Kind.SPIKE))
    with History(tmp_path / "h.db") as history:
        assert history.read("traffic") == series(3, Kind.SPIKE)


def test_time_range_includes_start_and_excludes_end(tmp_path: Path) -> None:
    points = series(10)
    with History(tmp_path / "h.db") as history:
        history.add("traffic", points)
        start, end = points[2].timestamp, points[5].timestamp
        assert history.read("traffic", start, end) == points[2:5]
        assert history.read("traffic", start=start) == points[2:]
        assert history.read("traffic", end=end) == points[:5]


def test_importing_twice_creates_no_duplicates(tmp_path: Path) -> None:
    with History(tmp_path / "h.db") as history:
        assert history.add("traffic", series(4)) == 4
        assert history.add("traffic", series(4)) == 0
        assert history.count("traffic") == 4


def test_importing_again_replaces_the_stored_values(tmp_path: Path) -> None:
    changed = Point(T0, 999.0, Kind.DROP)
    with History(tmp_path / "h.db") as history:
        history.add("traffic", series(2))
        assert history.add("traffic", [changed]) == 0
        assert history.read("traffic")[0] == changed


def test_metrics_do_not_mix(tmp_path: Path) -> None:
    with History(tmp_path / "h.db") as history:
        history.add("traffic", series(3))
        history.add("errors", series(2))
        assert history.metrics() == ["errors", "traffic"]
        assert history.count("traffic") == 3
        assert history.read("unknown") == []


def test_csv_round_trip_keeps_labels(tmp_path: Path) -> None:
    points = series(3) + [Point(T0 + timedelta(hours=1), 42.5, Kind.DRIFT)]
    write_csv(points, tmp_path / "x.csv")
    assert read_csv(tmp_path / "x.csv") == points


def test_csv_without_label_column_reads_real_data(tmp_path: Path) -> None:
    (tmp_path / "nab.csv").write_text("timestamp,value\n2014-07-01 00:00:00,10844\n")
    assert read_csv(tmp_path / "nab.csv") == [Point(datetime(2014, 7, 1), 10844.0, None)]


def test_csv_errors_name_the_line(tmp_path: Path) -> None:
    (tmp_path / "bad.csv").write_text("timestamp,value\n2026-01-05T00:00,1\n2026-01-05T00:05,abc\n")
    with pytest.raises(ValueError, match=r"bad\.csv:3:"):
        read_csv(tmp_path / "bad.csv")
    (tmp_path / "cols.csv").write_text("time,value\n")
    with pytest.raises(ValueError, match="missing column"):
        read_csv(tmp_path / "cols.csv")


def test_commands_store_import_and_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, csv_file = str(tmp_path / "h.db"), str(tmp_path / "t.csv")
    simulate = ["simulate", "--weeks", "1", "--no-anomalies", "--out", csv_file]
    assert main([*simulate, "--store", "traffic", "--db", db]) == 0
    assert "2016 new points" in capsys.readouterr().out

    assert main(["import", "traffic", csv_file, "--db", db]) == 0
    assert ": 0 new in 'traffic'" in capsys.readouterr().out

    assert (
        main(
            ["show", "traffic", "--from", "2026-01-05T01:00", "--to", "2026-01-05T01:15"]
            + ["--db", db]
        )
        == 0
    )
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in lines] == [
        "2026-01-05T01:00",
        "2026-01-05T01:05",
        "2026-01-05T01:10",
    ]


def test_show_reports_an_unknown_metric(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "h.db"
    assert main(["show", "traffic", "--db", str(db)]) == 2
    assert not db.exists()
    with History(db) as history:
        history.add("errors", series(1))
    assert main(["show", "traffic", "--db", str(db)]) == 2
    assert "known: errors" in capsys.readouterr().err
