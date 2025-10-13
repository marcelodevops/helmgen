import pytest
from pathlib import Path
import subprocess
import sys

@pytest.fixture(scope="session")
def cli_path() -> list[str]:
    """Return the CLI command as a list."""
    return [sys.executable, "-m", "helmgen"]

@pytest.fixture
def temp_chart_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for Helm chart output."""
    out_dir = tmp_path / "chart"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir

@pytest.fixture
def sample_compose_file(tmp_path: Path) -> Path:
    """Generate a minimal Docker Compose file for testing."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """version: '3.9'
services:
  app:
    image: nginx:latest
    ports:
      - "8080:80"
"""
    )
    return compose
