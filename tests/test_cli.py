import subprocess
import sys

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
