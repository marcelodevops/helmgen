import subprocess
import sys

import yaml
import pytest

from helmgen.generator import (
    DEFAULT_RESOURCES,
    compose_resources_to_k8s,
    deep_merge,
    detect_ingress,
    detect_probes,
    detect_sensitive_env,
    generate_helm_chart,
    is_database,
    normalize_depends_on,
    normalize_networks,
    parse_compose_duration,
    parse_port_string,
)


def write_compose(tmp_path, services):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(yaml.safe_dump({"services": services}, sort_keys=False))
    return compose


class TestParsePortString:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("8080:80", (8080, 80)),
            ("8080:80/tcp", (8080, 80)),
            ("127.0.0.1:8080:80", (8080, 80)),
            ("443", (None, 443)),
            ("abc", (None, None)),
        ],
    )
    def test_parsing(self, raw, expected):
        assert parse_port_string(raw) == expected


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


class TestDetectIngress:
    def test_backtick_host_label(self):
        svc = {"ports": [], "labels": {"rule": "Host(`web.example.com`)"}}
        ingress = detect_ingress(svc)
        assert ingress["rules"][0]["host"] == "web.example.com"

    def test_host_colon_label(self):
        svc = {"ports": [], "labels": {"rule": "Host:api.example.com && PathPrefix:/api"}}
        ingress = detect_ingress(svc)
        assert ingress["rules"][0]["host"] == "api.example.com"

    def test_web_port_with_protocol(self):
        svc = {"ports": ["8080:80/tcp"], "labels": {}}
        ingress = detect_ingress(svc)
        assert ingress["rules"][0]["port"] == 80

    def test_tls_on_443(self):
        svc = {"ports": ["443:443"], "labels": {}}
        ingress = detect_ingress(svc)
        assert "tls" in ingress

    def test_no_ingress_without_web_port(self):
        svc = {"ports": ["5432:5432"], "labels": {}}
        assert detect_ingress(svc) is None

    def test_default_ingress_class(self):
        svc = {"ports": ["8080:80"], "labels": {}}
        assert detect_ingress(svc)["className"] == "nginx"

    def test_custom_ingress_class(self):
        svc = {"ports": ["8080:80"], "labels": {}}
        assert detect_ingress(svc, "traefik")["className"] == "traefik"

    def test_no_legacy_class_annotation(self):
        svc = {"ports": ["8080:80"], "labels": {}}
        assert "kubernetes.io/ingress.class" not in str(detect_ingress(svc))


class TestGenerateChart:
    def generate(self, tmp_path, services, **kwargs):
        compose = write_compose(tmp_path, services)
        out = tmp_path / "chart"
        opts = dict(secret_provider="internal", store_scope="namespace", reuse_store=None)
        opts.update(kwargs)
        generate_helm_chart(compose_path=compose, output_dir=out, **opts)
        values = yaml.safe_load((out / "values.yaml").read_text())
        return out, values

    def test_store_flags_written_to_values_for_externalsecret(self, tmp_path):
        _, values = self.generate(
            tmp_path,
            {"web": {"image": "nginx"}},
            secret_provider="externalsecret",
            store_scope="cluster",
            reuse_store="global-vault-store",
        )
        assert values["secretProvider"] == "externalsecret"
        assert values["storeScope"] == "cluster"
        assert values["reuseStore"] == "global-vault-store"

    def test_store_flags_absent_for_internal(self, tmp_path):
        _, values = self.generate(tmp_path, {"web": {"image": "nginx"}})
        assert "storeScope" not in values
        assert "reuseStore" not in values

    def test_env_list_format(self, tmp_path):
        _, values = self.generate(
            tmp_path, {"web": {"image": "nginx", "environment": ["FOO=bar", "TOKEN=abc"]}}
        )
        assert values["services"]["web"]["env"] == {"FOO": "bar"}
        assert values["services"]["web"]["secrets"] == {"TOKEN": "<secret-from-values>"}

    def test_database_gets_storage_and_mount_paths(self, tmp_path):
        out, values = self.generate(
            tmp_path,
            {
                "db": {
                    "image": "postgres:14",
                    "volumes": ["db-data:/var/lib/postgresql/data"],
                }
            },
        )
        db = values["services"]["db"]
        assert db["storage"] is True
        assert db["storageSize"] == "5Gi"
        assert db["storagePaths"] == ["/var/lib/postgresql/data"]
        assert (out / "templates" / "statefulset.yaml").exists()

    def test_protocol_suffix_port_does_not_crash(self, tmp_path):
        _, values = self.generate(
            tmp_path, {"web": {"image": "nginx", "ports": ["8080:80/tcp"]}}
        )
        assert values["services"]["web"]["ports"] == [
            {"containerPort": 80, "published": 8080}
        ]

    def test_helpers_template_copied(self, tmp_path):
        out, _ = self.generate(tmp_path, {"web": {"image": "nginx"}})
        assert (out / "templates" / "_helpers.tpl").exists()

    def test_default_resources_written(self, tmp_path):
        _, values = self.generate(tmp_path, {"web": {"image": "nginx"}})
        assert values["resources"] == DEFAULT_RESOURCES

    def test_replicas_mapped(self, tmp_path):
        _, values = self.generate(tmp_path, {"web": {"image": "nginx", "replicas": 3}})
        assert values["services"]["web"]["replicas"] == 3

    def test_command_string_shlex_split(self, tmp_path):
        _, values = self.generate(
            tmp_path, {"worker": {"image": "python", "command": "python -c 'print(1)'"}}
        )
        assert values["services"]["worker"]["command"] == ["python", "-c", "print(1)"]

    def test_command_list_passthrough(self, tmp_path):
        _, values = self.generate(
            tmp_path, {"worker": {"image": "python", "args": ["run", "--fast"]}}
        )
        assert values["services"]["worker"]["args"] == ["run", "--fast"]

    def test_deploy_resources_mapped_to_k8s(self, tmp_path):
        _, values = self.generate(
            tmp_path,
            {
                "web": {
                    "image": "nginx",
                    "deploy": {"resources": {"limits": {"cpus": "0.5", "memory": "256M"}}},
                }
            },
        )
        assert values["services"]["web"]["resources"] == {
            "limits": {"cpu": "500m", "memory": "256M"}
        }

    def test_healthcheck_becomes_probes(self, tmp_path):
        _, values = self.generate(
            tmp_path,
            {
                "web": {
                    "image": "nginx",
                    "healthcheck": {
                        "test": ["CMD", "curl", "-f", "http://localhost/healthz"]
                    },
                }
            },
        )
        web = values["services"]["web"]
        assert web["livenessProbe"]["httpGet"]["path"] == "/healthz"
        assert web["readinessProbe"] == web["livenessProbe"]

    def test_ingress_class_flows_into_values(self, tmp_path):
        _, values = self.generate(
            tmp_path,
            {"web": {"image": "nginx", "ports": ["80:80"]}},
            ingress_class="traefik",
        )
        assert values["services"]["web"]["ingress"]["className"] == "traefik"


class TestComposeResources:
    def test_cpus_converted_to_millicores(self):
        assert compose_resources_to_k8s({"limits": {"cpus": "2.0"}}) == {
            "limits": {"cpu": "2000m"}
        }

    def test_empty(self):
        assert compose_resources_to_k8s({}) == {}


class TestTopologyNormalization:
    def test_depends_on_forms(self):
        assert normalize_depends_on("db") == ["db"]
        assert normalize_depends_on(["db", "cache"]) == ["db", "cache"]
        assert normalize_depends_on({"db": {"condition": "service_healthy"}}) == ["db"]
        assert normalize_depends_on(None) == []

    def test_networks_forms(self):
        assert normalize_networks(["frontend"]) == ["frontend"]
        assert normalize_networks({"frontend": {}}) == ["frontend"]
        assert normalize_networks(None) == []

    def test_deps_and_networks_in_values(self, tmp_path):
        gen = TestGenerateChart()
        _, values = gen.generate(
            tmp_path,
            {
                "db": {"image": "postgres", "ports": ["5432:5432"]},
                "web": {
                    "image": "nginx",
                    "depends_on": ["db"],
                    "networks": ["frontend"],
                },
            },
        )
        assert values["services"]["web"]["dependsOn"] == ["db"]
        assert values["services"]["web"]["networks"] == ["frontend"]
        assert values["waitOnDependencies"] is False


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

    def test_start_period_maps_to_initial_delay(self):
        svc = {"healthcheck": {"test": "curl http://localhost/h", "start_period": "10s"}}
        assert detect_probes(svc)["initialDelaySeconds"] == 10

    @pytest.mark.parametrize(
        "raw, seconds",
        [("30s", 30), ("500ms", 1), ("2m", 120), ("1h", 3600), ("nope", None)],
    )
    def test_duration_parsing(self, raw, seconds):
        assert parse_compose_duration(raw) == seconds


class TestValuesOverlay:
    def test_deep_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": [1], "c": 3}
        overlay = {"a": {"y": 3, "z": 4}, "b": [2], "d": 5}
        assert deep_merge(base, overlay) == {
            "a": {"x": 1, "y": 3, "z": 4},
            "b": [2],
            "c": 3,
            "d": 5,
        }

    def _write_inputs(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            yaml.safe_dump(
                {"services": {"web": {"image": "nginx", "environment": ["FOO=bar"]}}},
                sort_keys=False,
            )
        )
        overlay = tmp_path / "overlay.yml"
        overlay.write_text(
            yaml.safe_dump(
                {
                    "resources": {"limits": {"memory": "1Gi"}},
                    "services": {"web": {"replicas": 5}},
                    "waitOnDependencies": True,
                }
            )
        )
        return compose, overlay

    def test_overlay_merges_into_generated_values(self, tmp_path):
        compose, overlay = self._write_inputs(tmp_path)
        out = tmp_path / "chart"
        generate_helm_chart(
            compose_path=compose,
            output_dir=out,
            secret_provider="internal",
            store_scope="namespace",
            reuse_store=None,
            values_overlay=str(overlay),
        )
        values = yaml.safe_load((out / "values.yaml").read_text())
        web = values["services"]["web"]
        assert web["replicas"] == 5
        assert web["env"] == {"FOO": "bar"}  # generated keys survive
        assert values["resources"]["limits"]["memory"] == "1Gi"
        assert values["resources"]["requests"]["cpu"] == DEFAULT_RESOURCES["requests"]["cpu"]
        assert values["waitOnDependencies"] is True

    def test_cli_accepts_values_overlay(self, tmp_path):
        compose, overlay = self._write_inputs(tmp_path)
        out = tmp_path / "cli-chart"
        result = subprocess.run(
            [sys.executable, "-m", "helmgen", str(compose), "-o", str(out),
             "--values-overlay", str(overlay)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        values = yaml.safe_load((out / "values.yaml").read_text())
        assert values["services"]["web"]["replicas"] == 5

    def test_missing_overlay_file_exits(self, tmp_path):
        compose, _ = self._write_inputs(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "helmgen", str(compose), "-o", str(tmp_path / "c"),
             "--values-overlay", str(tmp_path / "nope.yml")],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "not found" in result.stderr.lower()


