from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.alerting import Alert, Alerter, raise_alerts
from anomaly.detect import Flag, flag_deviations
from anomaly.model import Kind, Point
from anomaly.simulate import DEFAULT_START, default_anomalies, simulate
from anomaly.storage import History

T0 = datetime(2026, 1, 5)


def flag(i: int, z: float = 5.0, label: Kind | None = None) -> Flag:
    return Flag(T0 + i * timedelta(minutes=5), 100 + z, 100, z, label)


def feed(pattern: str, confirm: int = 2, quiet: int = 3) -> list[int]:
    """Feed a pattern such as '.xx..x' (x = flagged point) and return where alerts fire."""
    alerter = Alerter(confirm, quiet)
    return [i for i, c in enumerate(pattern) if alerter.feed(flag(i) if c == "x" else None)]


def test_isolated_flagged_points_never_alert() -> None:
    assert feed(".x.x..x.x.") == []


def test_n_flagged_points_in_a_row_raise_one_alert() -> None:
    assert feed("..xx.....") == [3]
    assert feed("..xxx....", confirm=3) == [4]
    assert feed("..xx.....", confirm=3) == []


def test_an_ongoing_incident_does_not_alert_again() -> None:
    assert feed(".xxxxxxxx.") == [2]
    assert feed(".xx.x.xx.xx...") == [2]  # never 3 calm points in a row: still the same incident


def test_a_new_incident_can_alert_once_the_previous_one_ended() -> None:
    assert feed(".xx...xx", quiet=3) == [2, 7]
    assert feed(".xx..xx", quiet=3) == [2]  # only 2 calm points: the incident was still open


def test_confirm_one_alerts_on_every_new_incident() -> None:
    assert feed(".x...x.x", confirm=1, quiet=3) == [1, 5]


@pytest.mark.parametrize(("confirm", "quiet"), [(0, 3), (2, 0)])
def test_rejects_bad_settings(confirm: int, quiet: int) -> None:
    with pytest.raises(ValueError):
        Alerter(confirm, quiet)


def test_alert_describes_its_incident() -> None:
    alert = Alert((flag(4, z=3.5), flag(5, z=-40.0, label=Kind.DROP), flag(6, z=12.0)))
    assert alert.start == flag(4).timestamp
    assert alert.raised_at == flag(6).timestamp
    assert alert.peak.z == -40.0
    assert alert.label == Kind.DROP


def test_every_simulated_spike_and_drop_raises_an_alert() -> None:
    anomalies = default_anomalies(DEFAULT_START, 4)
    points = simulate(DEFAULT_START, 4, 42, anomalies)
    alerts = raise_alerts(points, flag_deviations(points))
    for anomaly in anomalies:
        assert any(anomaly.covers(alert.raised_at) for alert in alerts), anomaly


def test_alert_command_prints_and_appends_to_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, log = tmp_path / "h.db", tmp_path / "logs" / "alerts.log"
    values = [10, 12, 10, 12, 10, 12, 30, 100, 11, 30, 11]  # 30 and 100 flagged in a row
    points = [Point(T0 + i * timedelta(minutes=5), v, None) for i, v in enumerate(values)]
    with History(db) as history:
        history.add("m", points)
    args = ["alert", "m", "--window", "6", "--db", str(db), "--log", str(log)]

    assert main(args) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:5] == ["2026-01-05T00:35", "ALERT", "m", "since", "2026-01-05T00:30"]
    assert lines[1] == f"1 alerts (confirm 2, quiet 3), logged to {log}"
    assert log.read_text().splitlines() == [lines[0]]

    assert main(args) == 0  # a second run appends: the log keeps every run
    assert len(log.read_text().splitlines()) == 2

    assert main([*args, "--confirm", "3"]) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("0 alerts (confirm 3, quiet 3)")
