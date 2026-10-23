import subprocess
import sys
from pathlib import Path

import pytest

from anomaly.__main__ import main


def test_help_runs_as_a_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "anomaly", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage: anomaly" in result.stdout


def test_no_command_prints_help_and_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    assert "usage: anomaly" in capsys.readouterr().out


def test_closed_pipe_ends_quietly(tmp_path: Path) -> None:
    db = str(tmp_path / "history.db")
    csv = str(tmp_path / "traffic.csv")
    base = [sys.executable, "-m", "anomaly"]
    subprocess.run(
        [*base, "simulate", "--weeks", "1", "--out", csv, "--store", "traffic", "--db", db],
        capture_output=True,
        check=True,
    )
    with subprocess.Popen(
        [*base, "show", "traffic", "--db", db], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as proc:
        assert proc.stdout is not None
        proc.stdout.readline()
        proc.stdout.close()
        assert proc.stderr is not None
        assert proc.stderr.read() == b""
        assert proc.wait() == 0
