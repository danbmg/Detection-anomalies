import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.alerting import Alert, Detector, raise_alerts, raise_drift_alerts
from anomaly.detect import adapt_bands, flag_drifts, flag_outside
from anomaly.model import Point
from anomaly.normal import Method, normal_bands
from anomaly.simulate import DEFAULT_START, default_anomalies, simulate
from anomaly.storage import History, write_csv

T0 = datetime(2026, 1, 5)


def summary(alerts: list[Alert]) -> list[tuple[str, datetime, float]]:
    return [(alert.kind, alert.raised_at, alert.peak.k) for alert in alerts]


@pytest.mark.parametrize(
    ("method", "adaptive"),
    [(Method.ROLLING, False), (Method.SEASONAL, False), (Method.SEASONAL, True)],
)
def test_point_by_point_gives_the_same_alerts_as_the_whole_series(
    method: Method, adaptive: bool
) -> None:
    points = simulate(DEFAULT_START, 4, 42, default_anomalies(DEFAULT_START, 4))
    detector = Detector(method, adaptive=adaptive)
    live = [alert for point in points for alert in detector.feed(point)]

    bands = normal_bands(points, method)
    if adaptive:
        bands = adapt_bands(points, bands)
    batch = raise_alerts(points, flag_outside(points, bands))
    if method is Method.SEASONAL:
        batch += raise_drift_alerts(points, flag_drifts(points, bands))
    assert summary(live) == summary(sorted(batch, key=lambda alert: alert.raised_at))


@pytest.fixture
def csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    path = tmp_path / "traffic.csv"
    assert main(["simulate", "--out", str(path)]) == 0
    capsys.readouterr()
    return path


def test_watch_stores_each_point_and_raises_the_same_alerts_as_alert(
    csv: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, log = str(tmp_path / "h.db"), str(tmp_path / "alerts.log")
    settings = ["--method", "seasonal", "--db", db, "--log", log]
    assert main(["watch", "live", str(csv), "--speed", "0", *settings]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Watching 'live': 0 points in history, 8064 new points")
    assert out[1] == "2026-01-05  0 new points stored, 0 alerts"  # one line per new day
    assert out[-1].startswith("8064 new points stored, 4 alerts")
    with History(tmp_path / "h.db") as history:
        assert history.count("live") == 8064

    assert main(["alert", "live", *settings]) == 0
    batch = capsys.readouterr().out.splitlines()[:-1]
    assert [line for line in out if "ALERT" in line] == batch


def test_watch_only_replays_points_newer_than_the_history(
    csv: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "h.db"
    points = simulate(DEFAULT_START, 4, 42, default_anomalies(DEFAULT_START, 4))
    with History(db) as history:
        history.add("live", points[: 3 * 2016])  # 3 weeks already stored
    args = ["watch", "live", str(csv), "--speed", "0", "--method", "seasonal", "--db", str(db)]
    assert main([*args, "--log", str(tmp_path / "a.log")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "6048 points in history, 2016 new points" in out[0]
    # The 3 stored weeks set the band but their alerts are past: only the drift, in week 4.
    assert [line.split()[3] for line in out if "ALERT" in line] == ["drift"]

    assert main([*args, "--log", str(tmp_path / "a.log")]) == 0  # nothing new the second time
    assert "8064 points in history, 0 new points" in capsys.readouterr().out


def test_alerts_appear_while_the_stream_runs(tmp_path: Path) -> None:
    # 200 points with a spike in the middle, 5 minutes apart, replayed at 1 point every 10 ms:
    # about 2 seconds, and the alert is due after 1.
    values = [10.0, 12.0] * 50 + [30.0, 100.0] + [10.0, 12.0] * 49
    points = [Point(T0 + i * timedelta(minutes=5), v, None) for i, v in enumerate(values)]
    write_csv(points, tmp_path / "s.csv")
    command = [sys.executable, "-m", "anomaly", "watch", "m", str(tmp_path / "s.csv")]
    command += ["--speed", "30000", "--db", str(tmp_path / "h.db"), "--log", str(tmp_path / "a")]
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as proc:
        assert proc.stdout is not None
        line = next(line for line in proc.stdout if "ALERT" in line)
        assert line.startswith("2026-01-05T08:25  ALERT m  spike")
        assert proc.poll() is None  # the stream is still running when the alert shows up
        assert proc.wait() == 0


def test_watch_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["watch", "m", str(tmp_path / "missing.csv"), "--db", str(tmp_path / "h")]) == 2
    assert "missing.csv" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exit_info:
        main(["watch", "m", str(tmp_path / "missing.csv"), "--speed", "-1"])
    assert exit_info.value.code == 2
