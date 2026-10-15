"""Image-reference heuristics."""
from ..presets import DATABASE_REPOS

def image_repo(image):
    """Extract the repository from a docker image reference.

    'registry.example.com/library/postgres:14@sha256:abc...' -> 'postgres'
    """
    s = str(image or "").split("@", 1)[0]
    s = s.rsplit("/", 1)[-1]
    if ":" in s:
        s = s.split(":", 1)[0]
    return s

def is_database(image):
    """True only when the image repository itself is a known database.

    Matches on the repo name (bitnami/postgresql, mongo, redis:7-alpine) so
    sidecars like prometheus/postgres-exporter are not misdetected.
    """
    return image_repo(image) in DATABASE_REPOS

