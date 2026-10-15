"""Name-pattern heuristics over images, env vars and healthchecks."""
from .healthcheck import (
    detect_probes,
    map_healthcheck,
    parse_compose_duration,
    parse_healthcheck_probe,
)
from .images import image_repo, is_database
from .secrets import (
    detect_sensitive_env,
    is_sensitive_key,
)

__all__ = [
    "detect_probes",
    "detect_sensitive_env",
    "image_repo",
    "is_database",
    "is_sensitive_key",
    "map_healthcheck",
    "parse_compose_duration",
    "parse_healthcheck_probe",
]
