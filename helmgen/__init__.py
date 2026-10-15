"""
HelmGen — Auto-generate Helm charts from Docker Compose files.

This package provides:
  - A CLI entry point (helmgen.cli)
  - Compose parsing (helmgen.compose) and chart generation (helmgen.chart)
  - Detection heuristics (helmgen.heuristics) and ingress rules (helmgen.ingress)

Author: Marcelo Garcia
License: MIT
"""

__version__ = "0.3.0"

from .cli import main

if __name__ == "__main__":
    main()
