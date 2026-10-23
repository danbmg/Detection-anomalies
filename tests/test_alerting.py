from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anomaly.__main__ import main
from anomaly.alerting import Alert, Alerter, raise_alerts, raise_drift_alerts
from anomaly.detect import Flag, flag_deviations, flag_drifts
from anomaly.model import Kind, Point
from anomaly.normal import Method, normal_bands
from anomaly.simulate import (
    BATCH_END,
    BATCH_START,
    DEFAULT_START,
    Anomaly,
    default_anomalies,
    simulate,
)
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


def test_seasonal_method_raises_no_alert_on_a_clean_series() -> None:
    # Without anomalies every alert is false. The rolling method alerts on the nightly batch,
    # the seasonal one expects it.
    points = simulate(DEFAULT_START, 4, 42, [])
    rolling = raise_alerts(points, flag_deviations(points))
    assert any(BATCH_START <= alert.start.time() < BATCH_END for alert in rolling)
    assert raise_alerts(points, flag_deviations(points, method=Method.SEASONAL)) == []


@pytest.mark.parametrize("seed", [42, 1, 7])
def test_seasonal_method_still_alerts_on_every_spike_and_drop(seed: int) -> None:
    anomalies = default_anomalies(DEFAULT_START, 4)
    points = simulate(DEFAULT_START, 4, seed, anomalies)
    alerts = raise_alerts(points, flag_deviations(points, method=Method.SEASONAL))
    for anomaly in anomalies:
        if anomaly.kind is not Kind.DRIFT:  # a slow drift needs its own detector
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
    start = ["2026-01-05T00:35", "ALERT", "m", "spike", "since", "2026-01-05T00:30"]
    assert lines[0].split()[:6] == start
    assert lines[1] == f"1 alerts (rolling, confirm 2, quiet 3), logged to {log}"
    assert log.read_text().splitlines() == [lines[0]]

    assert main(args) == 0  # a second run appends: the log keeps every run
    assert len(log.read_text().splitlines()) == 2

    assert main([*args, "--confirm", "3"]) == 0
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith("0 alerts (rolling, confirm 3, quiet 3)")


def drift_alerts(seed: int, anomalies: list[Anomaly]) -> list[Alert]:
    points = simulate(DEFAULT_START, 4, seed, anomalies)
    return raise_drift_alerts(points, flag_drifts(points, normal_bands(points, Method.SEASONAL)))


@pytest.mark.parametrize("seed", [42, 1, 7])
def test_the_slow_drift_raises_one_drift_alert_while_it_lasts(seed: int) -> None:
    # The drift adds 50 % over 4 days, about 0.5 % per hour, under the 5 % noise of each point.
    anomalies = default_anomalies(DEFAULT_START, 4)
    drift = next(anomaly for anomaly in anomalies if anomaly.kind is Kind.DRIFT)
    alerts = drift_alerts(seed, anomalies)
    assert len(alerts) == 1  # it alarms again every 2 hours or so: still one incident
    assert alerts[0].kind is Kind.DRIFT
    assert drift.covers(alerts[0].raised_at)


@pytest.mark.parametrize("seed", [42, 1, 7])
def test_no_drift_alert_without_a_drift(seed: int) -> None:
    anomalies = default_anomalies(DEFAULT_START, 4)
    assert drift_alerts(seed, []) == []
    # Spikes and the drop leave their band: the z-score raises them, never CUSUM.
    no_drift = [anomaly for anomaly in anomalies if anomaly.kind is not Kind.DRIFT]
    assert drift_alerts(seed, no_drift) == []


def test_alert_command_tells_drifts_from_spikes_and_drops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, log = str(tmp_path / "h.db"), str(tmp_path / "alerts.log")
    assert main(["simulate", "--out", str(tmp_path / "t.csv"), "--store", "t", "--db", db]) == 0
    capsys.readouterr()
    assert main(["alert", "t", "--method", "seasonal", "--db", db, "--log", log]) == 0
    lines = capsys.readouterr().out.splitlines()
    # 4th column: what the detector saw; last one: the ground truth.
    assert [(line.split()[3], line.split()[-1]) for line in lines[:-1]] == [
        ("spike", "spike"),
        ("spike", "spike"),
        ("drop", "drop"),
        ("drift", "drift"),
    ]
    assert "cusum" in lines[3]

    assert main(["alert", "t", "--db", db, "--log", log]) == 0  # rolling: no CUSUM at all
    assert "drift" not in [line.split()[3] for line in capsys.readouterr().out.splitlines()[:-1]]
