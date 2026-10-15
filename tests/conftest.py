import pytest
from pathlib import Path
import sys

import yaml

from helmgen.chart import generate_helm_chart


def write_compose(tmp_path: Path, services: dict) -> Path:
    """Dump a services mapping to a compose file in tmp_path and return its path."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(yaml.safe_dump({"services": services}, sort_keys=False))
    return compose


def generate_chart(tmp_path: Path, services: dict, **kwargs):
    """Generate a chart from a services mapping; return (chart_dir, values)."""
    compose = write_compose(tmp_path, services)
    out = tmp_path / "chart"
    opts = dict(secret_provider="internal", store_scope="namespace", reuse_store=None,
                base_domain=None, sensitive_env=None)
    opts.update(kwargs)
    generate_helm_chart(compose_path=compose, output_dir=out, **opts)
    values = yaml.safe_load((out / "values.yaml").read_text())
    return out, values


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
