"""
HelmGen — Auto-generate Helm charts from Docker Compose files.

This package provides:
  - A CLI entry point (helmgen)
  - A generator that parses docker-compose.yml
  - Template rendering for Helm charts

Author: Marcelo Garcia
License: MIT
"""

__version__ = "0.1.0"

from .generator import main

if __name__ == "__main__":
    main()