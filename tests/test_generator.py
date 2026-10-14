import subprocess
import sys

import yaml
import pytest

from helmgen.generator import (
    DEFAULT_RESOURCES,
    FLAVOR_RESOURCES,
    SUPPORTED_FLAVORS,
    apply_flavor,
    compose_resources_to_k8s,
    deep_merge,
    detect_flavor,
    detect_ingress,
    detect_probes,
    detect_sensitive_env,
    generate_helm_chart,
    image_repo,
    is_database,
    is_sensitive_key,
    map_healthcheck,
    normalize_depends_on,
    normalize_labels,
    normalize_networks,
    normalize_port_entry,
    parse_compose_duration,
    parse_env_file,
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

    @pytest.mark.parametrize(
        "entry, expected",
        [
            ({"target": 8080, "published": "80", "protocol": "udp"},
             {"containerPort": 8080, "published": 80, "protocol": "UDP"}),
            ({"published": 8080}, None),
            ({"target": 443, "mode": "hostip"}, {"containerPort": 443, "mode": "hostip"}),
        ],
    )
    def test_long_syntax(self, entry, expected):
        assert normalize_port_entry(entry) == expected

    def test_unparsable_string_has_no_target(self):
        assert normalize_port_entry("not-a-port") is None


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


class TestNormalizeLabels:
    def test_list_form(self):
        assert normalize_labels(["a=b", "c=d"]) == {"a": "b", "c": "d"}

    def test_map_form(self):
        assert normalize_labels({"a": "b"}) == {"a": "b"}

    def test_flavor_from_list_labels(self):
        assert detect_flavor({"labels": ["helmgen.flavor=payara"]}) == "payara"


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

    def test_multi_host_v3_rule(self):
        svc = {
            "ports": ["80"],
            "labels": {"traefik.http.routers.app.rule": "Host(`a.com`, `b.com`)"},
        }
        ingress = detect_ingress(svc)
        assert [r["host"] for r in ingress["rules"]] == ["a.com", "b.com"]
        assert ingress["rules"][0]["port"] == 80

    def test_multi_host_v1_comma(self):
        svc = {"ports": [], "labels": {"rule": "Host:a.com,b.com"}}
        ingress = detect_ingress(svc)
        assert [r["host"] for r in ingress["rules"]] == ["a.com", "b.com"]

    def test_path_rule_becomes_rule_path(self):
        svc = {
            "ports": ["80"],
            "labels": {"traefik.http.routers.api.rule": "Host(`api.com`) && PathPrefix(`/v2`)"},
        }
        ingress = detect_ingress(svc)
        assert ingress["rules"][0]["path"] == "/v2"

    def test_tls_label_true_and_false(self):
        svc = {
            "ports": ["80"],
            "labels": {
                "traefik.http.routers.app.rule": "Host(`app.com`)",
                "traefik.http.routers.app.tls": "true",
            },
        }
        assert "tls" in detect_ingress(svc)
        svc["labels"]["traefik.http.routers.app.tls"] = "false"
        assert "tls" not in detect_ingress(svc)

    def test_tls_substring_label_does_not_enable(self):
        svc = {
            "ports": ["80"],
            "labels": {
                "traefik.enable": "true",
                "traefik.http.routers.app.rule": "Host(`app.com`)",
                "traefik.docker.network": "example-tls-backend",
            },
        }
        assert "tls" not in detect_ingress(svc)

    def test_tls_certresolver_label_enables(self):
        svc = {
            "ports": ["80"],
            "labels": {"traefik.http.routers.app.rule": "Host(`app.com`)",
                       "traefik.http.routers.app.tls.certresolver": "le"},
        }
        assert "tls" in detect_ingress(svc)

    def test_hyphenated_hostname_does_not_enable_tls(self):
        svc = {"ports": ["80"], "labels": {"rule": "Host(`foo-tls.example.com`)"}}
        ingress = detect_ingress(svc)
        assert ingress["rules"][0]["host"] == "foo-tls.example.com"
        assert "tls" not in ingress

    def test_tls_token_in_rule_enables(self):
        svc = {"ports": ["80"], "labels": {"traefik.http.routers.app.rule":
                                           "Host(`app.com`) && PathPrefix(`/`) && tls"}}
        assert "tls" in detect_ingress(svc)

    def test_https_entrypoint_router_enables_tls(self):
        svc = {"ports": ["80"], "labels": {"traefik.https.routers.app.rule": "Host(`app.com`)"}}
        assert "tls" in detect_ingress(svc)

    def test_ingress_port_is_container_not_published(self):
        svc = {"ports": ["1234:80"], "labels": {}}
        assert detect_ingress(svc)["rules"][0]["port"] == 80

    def test_base_domain_no_labels(self):
        svc = {"ports": ["8080"], "labels": {}}
        ingress = detect_ingress(svc, base_domain="app.example.com")
        assert ingress["rules"] == [{"host": "app.example.com", "path": "/", "port": 8080}]
        assert "tls" in ingress  # base domain implies HTTPS

    def test_base_domain_skips_non_web_ports(self):
        # databases and exporters must not be published through the gateway
        assert detect_ingress({"ports": ["5432"], "labels": {}},
                              base_domain="app.example.com") is None
        assert detect_ingress({"ports": [], "labels": {}},
                              base_domain="app.example.com") is None

    def test_base_domain_does_not_override_labels(self):
        svc = {"ports": ["80"], "labels": {"rule": "Host(`real.example.org`)"}}
        ingress = detect_ingress(svc, base_domain="app.example.com")
        assert ingress["rules"][0]["host"] == "real.example.org"

    def test_no_base_domain_no_ports_no_ingress(self):
        assert detect_ingress({"ports": [], "labels": {}}, base_domain=None) is None


class TestGenerateChart:
    def generate(self, tmp_path, services, **kwargs):
        compose = write_compose(tmp_path, services)
        out = tmp_path / "chart"
        opts = dict(secret_provider="internal", store_scope="namespace", reuse_store=None,
                    base_domain=None, sensitive_env=None)
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

    def test_env_file_merged_with_inline_precedence(self, tmp_path):
        envf = tmp_path / ".env"
        envf.write_text(
            "# comment\nFOO=from_env\nDB_PASSWORD='s3cret'\nexport BAR=baz\nMALFORMED\n"
        )
        _, values = self.generate(
            tmp_path,
            {"web": {"image": "nginx", "env_file": ".env", "environment": {"FOO": "inline"}}},
        )
        web = values["services"]["web"]
        assert web["env"] == {"FOO": "inline", "BAR": "baz"}
        assert web["secrets"] == {"DB_PASSWORD": "<secret-from-values>"}

    def test_env_file_map_form_missing_optional_ok(self, tmp_path):
        _, values = self.generate(
            tmp_path,
            {"web": {"image": "nginx", "env_file": [{"path": "absent.env", "required": False}]}},
        )
        assert values["services"]["web"]["env"] == {}

    def test_env_file_list_of_files_later_wins(self, tmp_path):
        (tmp_path / "a.env").write_text("X=1\nY=1")
        (tmp_path / "b.env").write_text("X=2")
        _, values = self.generate(
            tmp_path, {"web": {"image": "nginx", "env_file": ["a.env", "b.env"]}}
        )
        assert values["services"]["web"]["env"] == {"X": "2", "Y": "1"}

    def test_long_syntax_ports_normalized(self, tmp_path):
        _, values = self.generate(
            tmp_path,
            {"web": {"image": "nginx", "ports": [{"target": 8080, "published": "80",
                                                  "protocol": "udp"}]}},
        )
        assert values["services"]["web"]["ports"] == [
            {"containerPort": 8080, "published": 80, "protocol": "UDP"}
        ]

    def test_build_only_gets_placeholder_image(self, tmp_path):
        _, values = self.generate(
            tmp_path, {"api": {"build": {"context": "./api"}, "ports": ["8000"]}}
        )
        assert values["services"]["api"]["image"] == "api:local"

    def test_configs_and_secrets_do_not_crash(self, tmp_path, capsys):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(yaml.safe_dump({
            "services": {"web": {"image": "nginx",
                                 "configs": [{"source": "cfg", "target": "/x"}]}},
            "configs": {"cfg": {"file": "./cfg.txt"}},
        }))
        out = tmp_path / "chart"
        generate_helm_chart(
            compose_path=compose, output_dir=out,
            secret_provider="internal", store_scope="namespace", reuse_store=None,
        )
        err = capsys.readouterr().err
        assert "configs" in err and "secrets" not in err  # only configs warning here
        values = yaml.safe_load((out / "values.yaml").read_text())
        assert values["services"]["web"]["image"] == "nginx"

    def test_base_domain_flows_into_values(self, tmp_path):
        _, values = self.generate(
            tmp_path,
            {"web": {"image": "nginx", "ports": ["8080"]}},
            base_domain="web.example.com",
        )
        assert values["services"]["web"]["ingress"]["rules"][0]["host"] == "web.example.com"

    def test_sensitive_env_flag_flows_into_values(self, tmp_path):
        _, values = self.generate(
            tmp_path,
            {"web": {"image": "nginx", "environment": {"PUBLIC_KEY": "ssh-rsa AAAA"}}},
            sensitive_env="PUBLIC_KEY",
        )
        assert values["services"]["web"]["secrets"] == {"PUBLIC_KEY": "<secret-from-values>"}

    def test_exec_healthcheck_maps_readiness_only_in_values(self, tmp_path):
        _, values = self.generate(
            tmp_path,
            {"db": {"image": "postgres:14",
                    "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U app"]}}},
        )
        db = values["services"]["db"]
        assert "readinessProbe" in db
        assert "livenessProbe" not in db

    def test_labels_list_form_ingress_host(self, tmp_path):
        _, values = self.generate(
            tmp_path,
            {"web": {"image": "nginx", "ports": ["80"],
                     "labels": ["traefik.http.routers.web.rule=Host(`w.com`)"]}},
        )
        assert values["services"]["web"]["ingress"]["rules"][0]["host"] == "w.com"


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


def test_parse_env_file_warns_malformed(tmp_path, capsys):
    f = tmp_path / "e.env"
    f.write_text('A=1\nB = "quoted" \nnoequals\n')
    assert parse_env_file(f) == {"A": "1", "B": "quoted"}
    assert "malformed" in capsys.readouterr().err


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


class TestValuesDocHeader:
    def test_header_written_and_yaml_parses(self, tmp_path):
        gen = TestGenerateChart()
        out, values = gen.generate(tmp_path, {"web": {"image": "nginx"}})
        text = (out / "values.yaml").read_text()
        assert text.startswith("# Generated by helmgen")
        for knob in ("secretProvider", "storeScope", "reuseStore", "resources",
                     "waitOnDependencies", "probePath"):
            assert knob in text
        assert values["services"]["web"]["image"] == "nginx"


class TestFlavors:
    def test_supported_flavors_defined(self):
        assert "spring-boot" in SUPPORTED_FLAVORS
        assert "payara" in SUPPORTED_FLAVORS
        assert "generic" in SUPPORTED_FLAVORS

    def test_detect_flavor_from_labels(self):
        svc_payara = {"labels": {"helmgen.flavor": "payara"}}
        assert detect_flavor(svc_payara) == "payara"

        svc_sb = {"labels": {"helmgen.flavor": "springboot"}}
        assert detect_flavor(svc_sb) == "spring-boot"

        svc_spring = {"labels": {"flavor": "spring"}}
        assert detect_flavor(svc_spring) == "spring-boot"

    def test_detect_flavor_from_image(self):
        assert detect_flavor({"image": "payara/server-full:6.2023.4"}) == "payara"
        assert detect_flavor({"image": "payara/micro:6.2023.4"}) == "payara"
        assert detect_flavor({"image": "myorg/spring-boot-app:1.0"}) == "spring-boot"
        assert detect_flavor({"image": "ghcr.io/org/my-springboot-service:v2"}) == "spring-boot"

    def test_detect_flavor_fallback_to_default(self):
        assert detect_flavor({"image": "nginx:alpine"}) == "generic"
        assert detect_flavor({"image": "nginx:alpine"}, default_flavor="payara") == "payara"
        assert detect_flavor({"image": "nginx:alpine"}, default_flavor="springboot") == "spring-boot"

    def test_spring_boot_flavor_presets(self, tmp_path):
        gen = TestGenerateChart()
        _, values = gen.generate(
            tmp_path,
            {"app": {"image": "openjdk:17", "ports": ["8080:8080"]}},
            flavor="spring-boot",
        )
        assert values["flavor"] == "spring-boot"
        app = values["services"]["app"]
        assert app["flavor"] == "spring-boot"
        assert app["livenessProbe"]["httpGet"]["path"] == "/actuator/health/liveness"
        assert app["livenessProbe"]["httpGet"]["port"] == 8080
        assert app["readinessProbe"]["httpGet"]["path"] == "/actuator/health/readiness"
        assert app["readinessProbe"]["httpGet"]["port"] == 8080
        assert app["startupProbe"]["httpGet"]["path"] == "/actuator/health/liveness"
        assert app["startupProbe"]["failureThreshold"] == 20
        assert app["resources"] == FLAVOR_RESOURCES["spring-boot"]

    def test_payara_flavor_presets(self, tmp_path):
        gen = TestGenerateChart()
        _, values = gen.generate(
            tmp_path,
            {"app": {"image": "payara/server-full:6.2023.4", "ports": ["4848:4848", "8080:8080"]}},
        )
        app = values["services"]["app"]
        assert app["flavor"] == "payara"
        assert app["livenessProbe"]["httpGet"]["path"] == "/health/live"
        assert app["livenessProbe"]["httpGet"]["port"] == 8080  # skips admin port 4848
        assert app["readinessProbe"]["httpGet"]["path"] == "/health/ready"
        assert app["readinessProbe"]["httpGet"]["port"] == 8080
        assert app["startupProbe"]["httpGet"]["path"] == "/health/live"
        assert app["startupProbe"]["failureThreshold"] == 30
        assert app["resources"] == FLAVOR_RESOURCES["payara"]

    def test_custom_healthcheck_preserved_with_flavor(self, tmp_path):
        gen = TestGenerateChart()
        _, values = gen.generate(
            tmp_path,
            {
                "app": {
                    "image": "my-spring-boot-app:1.0",
                    "healthcheck": {
                        "test": ["CMD", "curl", "-f", "http://localhost:8080/custom/health"],
                        "interval": "10s",
                    },
                }
            },
        )
        app = values["services"]["app"]
        assert app["livenessProbe"]["httpGet"]["path"] == "/custom/health"
        assert app["readinessProbe"]["httpGet"]["path"] == "/custom/health"
        # startup probe is still provided for slow JVM boot
        assert app["startupProbe"]["httpGet"]["path"] == "/actuator/health/liveness"

    def test_compose_deploy_resources_override_flavor_defaults(self, tmp_path):
        gen = TestGenerateChart()
        _, values = gen.generate(
            tmp_path,
            {
                "app": {
                    "image": "payara/micro:6.2023.4",
                    "deploy": {"resources": {"limits": {"memory": "3Gi"}}},
                }
            },
        )
        app = values["services"]["app"]
        assert app["resources"] == {"limits": {"memory": "3Gi"}}

    def test_invalid_flavor_raises(self, tmp_path):
        gen = TestGenerateChart()
        with pytest.raises(ValueError, match="Unsupported flavor"):
            gen.generate(tmp_path, {"web": {"image": "nginx"}}, flavor="unknown-flavor")

    def test_cli_accepts_flavor(self, tmp_path):
        compose = write_compose(tmp_path, {"web": {"image": "nginx"}})
        out = tmp_path / "cli-flavor-chart"
        result = subprocess.run(
            [sys.executable, "-m", "helmgen", str(compose), "-o", str(out), "--flavor", "payara"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        values = yaml.safe_load((out / "values.yaml").read_text())
        assert values["flavor"] == "payara"
        assert values["services"]["web"]["flavor"] == "payara"


class TestChartMetadata:
    def test_custom_chart_metadata(self, tmp_path):
        compose = write_compose(tmp_path, {"web": {"image": "nginx"}})
        out = tmp_path / "custom-chart"
        generate_helm_chart(
            compose_path=compose,
            output_dir=out,
            secret_provider="internal",
            store_scope="namespace",
            reuse_store=None,
            chart_version="1.2.3",
            app_version="2.4.0",
            chart_name="my-custom-app",
        )
        chart_yaml = yaml.safe_load((out / "Chart.yaml").read_text())
        assert chart_yaml["version"] == "1.2.3"
        assert chart_yaml["appVersion"] == "2.4.0"
        assert chart_yaml["name"] == "my-custom-app"

    def test_cli_accepts_chart_metadata_flags(self, tmp_path):
        compose = write_compose(tmp_path, {"web": {"image": "nginx"}})
        out = tmp_path / "cli-meta-chart"
        result = subprocess.run(
            [
                sys.executable, "-m", "helmgen", str(compose), "-o", str(out),
                "--chart-version", "0.5.0",
                "--app-version", "3.0.1",
                "--chart-name", "awesome-app",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        chart_yaml = yaml.safe_load((out / "Chart.yaml").read_text())
        assert chart_yaml["version"] == "0.5.0"
        assert chart_yaml["appVersion"] == "3.0.1"
        assert chart_yaml["name"] == "awesome-app"





