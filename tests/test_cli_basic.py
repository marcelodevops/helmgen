import subprocess
import sys

def test_help(cli_path):
    """Verify `helmgen --help` runs correctly."""
    result = subprocess.run(cli_path + ["--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()

def test_version_flag(cli_path):
    """Verify `helmgen --version` runs if implemented."""
    result = subprocess.run(cli_path + ["--version"], capture_output=True, text=True)
    assert result.returncode == 0 or result.returncode == 2  # argparse may exit(2) if missing
