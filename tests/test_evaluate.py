from datetime import datetime, timedelta

import pytest

from anomaly.__main__ import main
from anomaly.alerting import Alert
from anomaly.detect import Flag
from anomaly.evaluate import Score, Window, score_alerts, true_windows
from anomaly.model import Kind, Point

T0 = datetime(2026, 1, 5)
LABELS = [None, Kind.SPIKE, Kind.SPIKE, None, Kind.DROP, Kind.DROP, None, Kind.DRIFT, Kind.DRIFT]


def at(i: int) -> datetime:
    return T0 + i * timedelta(minutes=5)


POINTS = [Point(at(i), 100.0, label) for i, label in enumerate(LABELS)]


def alert_at(i: int) -> Alert:
    return Alert((Flag(at(i), 150, 100, 5, LABELS[i]),))


def test_true_windows_are_runs_of_the_same_label() -> None:
    assert true_windows(POINTS) == [
        Window(Kind.SPIKE, at(1), at(2)),
        Window(Kind.DROP, at(4), at(5)),
        Window(Kind.DRIFT, at(7), at(8)),
    ]
    # A change of label starts a new anomaly, even without a normal point in between.
    touching = [Point(at(0), 1.0, Kind.SPIKE), Point(at(1), 1.0, Kind.DROP)]
    assert [window.kind for window in true_windows(touching)] == [Kind.SPIKE, Kind.DROP]


def test_an_alert_is_correct_when_raised_inside_a_true_anomaly() -> None:
    # Two alerts in the spike (two correct alerts, one anomaly found), one between the spike and
    # the drop (false), one on the last point of the drop (the end is included), none in the drift.
    result = score_alerts(POINTS, [alert_at(1), alert_at(2), alert_at(3), alert_at(5)])
    assert result == Score(
        alerts=4,
        false=1,
        anomalies=3,
        missed=(Kind.DRIFT,),
        delays=(timedelta(0), timedelta(minutes=5)),
    )
    assert result.precision == 3 / 4
    assert result.recall == 2 / 3
    assert result.mean_delay == timedelta(minutes=2.5)


def test_scores_of_several_series_add_up() -> None:
    one = score_alerts(POINTS, [alert_at(3)])  # one false alert, three anomalies missed
    assert Score() + one == one
    both = one + one
    assert (both.alerts, both.false, both.anomalies, len(both.missed)) == (2, 2, 6, 6)
    assert (both.precision, both.recall, both.mean_delay) == (0, 0, None)


def test_nothing_to_divide_gives_no_rate() -> None:
    assert (Score().precision, Score().recall, Score().mean_delay) == (None, None, None)


def run_evaluate(capsys: pytest.CaptureFixture[str], *args: str) -> list[str]:
    assert main(["evaluate", *args]) == 0
    return capsys.readouterr().out.splitlines()


def test_evaluate_command_compares_every_method(capsys: pytest.CaptureFixture[str]) -> None:
    lines = run_evaluate(capsys)
    assert lines[0].startswith("Simulated traffic, 4 weeks, seed 42: 4 true anomalies each")
    rows = {line[:22].strip(): line[22:].split() for line in lines[4:9]}
    assert list(rows) == [
        "fixed threshold",
        "rolling",
        "rolling, adaptive k",
        "seasonal",
        "seasonal, adaptive k",
    ]
    # alerts, false, missed, precision, recall, then the delay and the missed kinds
    assert rows["fixed threshold"][2:5] == ["2", "100%", "50%"]
    assert rows["fixed threshold"][-2:] == ["spike,", "drop"]  # the night spike and the drop
    assert rows["rolling"][:5] == ["43", "37", "0", "14%", "100%"]
    assert rows["seasonal"][:5] == ["4", "0", "0", "100%", "100%"]


def test_evaluate_command_is_reproducible(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_evaluate(capsys, "--seed", "7") == run_evaluate(capsys, "--seed", "7")
    lines = run_evaluate(capsys, "--runs", "2")
    assert lines[0].startswith("Simulated traffic, 4 weeks, seeds 42 to 43")
    assert lines[7].split()[:2] == ["seasonal", "8"]  # 2 series of 4 anomalies, 4 alerts each
