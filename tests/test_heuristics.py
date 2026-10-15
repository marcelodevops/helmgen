"""Tests for name-pattern heuristics (helmgen.heuristics)."""

import pytest

from helmgen.heuristics import (
    detect_probes,
    detect_sensitive_env,
    image_repo,
    is_database,
    is_sensitive_key,
    map_healthcheck,
    parse_compose_duration,
)


class TestSensitiveEnv:
    def test_splits_sensitive_and_normal(self):
        normal, sensitive = detect_sensitive_env(
            {"APP_ENV": "prod", "SECRET_KEY": "x", "POSTGRES_PASSWORD": "y"}
        )
        assert normal == {"APP_ENV": "prod"}
        assert set(sensitive) == {"SECRET_KEY", "POSTGRES_PASSWORD"}


class TestIsDatabase:
    @pytest.mark.parametrize(
        "image, expected",
        [("postgres:14", True), ("redis:7", True), ("nginx:alpine", False)],
    )
    def test_detection(self, image, expected):
        assert is_database(image) is expected

    @pytest.mark.parametrize(
        "image, expected",
        [
            ("bitnami/postgresql:16", True),
            ("mongo:7", True),
            ("prometheuscommunity/postgres-exporter", False),
            ("otel/opentelemetry-collector-contrib:0.90.0", False),
            ("mysql-exporter:1.2", False),
            ("redis-commander", False),
        ],
    )
    def test_repo_name_matching(self, image, expected):
        assert is_database(image) is expected

    @pytest.mark.parametrize(
        "image, repo",
        [
            ("registry:5000/library/postgres:14@sha256:abcdef", "postgres"),
            ("nginx", "nginx"),
            ("redis:7-alpine", "redis"),
            ("gcr.io/proj/my-app:1.0", "my-app"),
        ],
    )
    def test_image_repo_extraction(self, image, repo):
        assert image_repo(image) == repo


class TestSensitiveKeys:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("SECRET_KEY", True),
            ("POSTGRES_PASSWORD", True),
            ("DB_PASS", True),
            ("API_TOKEN", True),
            ("PUBLIC_KEY", False),
            ("SSH_PUBLIC_KEY", False),
            ("KEYCLOAK_PUBLIC_KEY", False),
            ("KEYSTORE_PASS", True),  # exclusion never cancels a real secret keyword
            ("PGPASSWORD", True),  # suffix match, no separator
            ("PASSIVE_MODE", False),  # PASS only as a prefix of an unrelated word
        ],
    )
    def test_is_sensitive_key(self, name, expected):
        assert is_sensitive_key(name) is expected

    def test_extra_sensitive_overrides_exclusion(self):
        assert is_sensitive_key("PUBLIC_KEY") is False
        assert is_sensitive_key("PUBLIC_KEY", extra=["public_key"]) is True

    def test_detect_sensitive_env_with_extra(self):
        normal, sensitive = detect_sensitive_env(
            {"SITE_KEY": "pub-id", "OTHER": "1"}, extra_sensitive=["SITE_KEY"]
        )
        assert sensitive == {"SITE_KEY": "<secret-from-values>"}
        assert normal == {"OTHER": "1"}


class TestDetectProbes:
    def test_cmd_curl_url_becomes_http_get(self):
        svc = {"healthcheck": {
            "test": ["CMD", "curl", "-f", "http://localhost:8080/healthz"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 4,
        }}
        probe = detect_probes(svc)
        assert probe["httpGet"] == {"path": "/healthz", "port": 8080}
        assert probe["periodSeconds"] == 30
        assert probe["timeoutSeconds"] == 5
        assert probe["failureThreshold"] == 4

    def test_cmd_shell_string_parsed(self):
        svc = {"healthcheck": {"test": ["CMD-SHELL", "wget -q http://localhost/ || exit 1"]}}
        probe = detect_probes(svc)
        assert probe["httpGet"] == {"path": "/", "port": 80}

    def test_plain_test_string_with_url(self):
        svc = {"healthcheck": {"test": "curl -fs https://localhost:443/status"}}
        assert detect_probes(svc)["httpGet"] == {"path": "/status", "port": 443}

    def test_non_http_becomes_exec(self):
        svc = {"healthcheck": {"test": ["CMD", "pg_isready", "-U", "user"]}}
        assert detect_probes(svc) == {"exec": {"command": ["pg_isready", "-U", "user"]}}

    def test_disable_and_missing(self):
        assert detect_probes({"healthcheck": {"disable": True, "test": "curl x"}}) is None
        assert detect_probes({}) is None

    def test_start_period_excluded_from_base_probe(self):
        # start_period no longer delays liveness; see TestMapHealthcheck
        svc = {"healthcheck": {"test": "curl http://localhost/h", "start_period": "10s"}}
        probe = detect_probes(svc)
        assert "initialDelaySeconds" not in probe

    @pytest.mark.parametrize(
        "raw, seconds",
        [("30s", 30), ("500ms", 1), ("2m", 120), ("1h", 3600), ("nope", None)],
    )
    def test_duration_parsing(self, raw, seconds):
        assert parse_compose_duration(raw) == seconds


class TestMapHealthcheck:
    def test_http_probe_maps_to_both(self):
        svc = {"healthcheck": {"test": "curl -f http://localhost:8080/healthz"}}
        probes = map_healthcheck(svc)
        assert probes["livenessProbe"]["httpGet"] == {"path": "/healthz", "port": 8080}
        assert probes["readinessProbe"] == probes["livenessProbe"]
        assert "startupProbe" not in probes

    def test_exec_probe_maps_to_readiness_only(self):
        svc = {"healthcheck": {"test": ["CMD", "pg_isready", "-U", "user"]}}
        probes = map_healthcheck(svc)
        assert probes == {
            "readinessProbe": {"exec": {"command": ["pg_isready", "-U", "user"]}}
        }

    def test_start_period_becomes_startup_probe(self):
        svc = {"healthcheck": {"test": "curl http://localhost/h", "start_period": "30s"}}
        probes = map_healthcheck(svc)
        startup = probes["startupProbe"]
        assert startup["initialDelaySeconds"] == 30
        assert startup["failureThreshold"] == 120
        assert "initialDelaySeconds" not in probes["livenessProbe"]

    def test_disabled_returns_empty(self):
        assert map_healthcheck({"healthcheck": {"disable": True, "test": "curl x"}}) == {}
        assert map_healthcheck({}) == {}
