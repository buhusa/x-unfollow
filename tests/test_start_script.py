import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = PROJECT_ROOT / "start.sh"


def test_start_script_is_executable():
    assert START_SCRIPT.is_file()
    assert os.access(START_SCRIPT, os.X_OK)


def test_start_script_has_valid_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(START_SCRIPT)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
