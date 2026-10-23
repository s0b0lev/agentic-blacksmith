import subprocess
import sys
from pathlib import Path

from blacksmith.runner import subprocess_runner

AGENT_DIR = Path(__file__).resolve().parent.parent


def test_version_flag():
    proc = subprocess.run(
        [sys.executable, "-m", "blacksmith", "--version"],
        cwd=AGENT_DIR,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "0.1.0"


def test_subprocess_runner_timeout():
    result = subprocess_runner(["sleep", "2"], timeout=1)
    assert result.code == 124
