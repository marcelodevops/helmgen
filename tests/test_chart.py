"""Tests for chart and values.yaml generation (helmgen.chart)."""

import yaml
import pytest
import subprocess
import sys

from helmgen.presets import DEFAULT_RESOURCES
from helmgen.chart import deep_merge, generate_helm_chart
from conftest import generate_chart, write_compose


class TestGenerateChart:
    def test_store_flags_written_to_values_for_externalsecret(self, tmp_path):
        _, values = generate_chart(
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
        _, values = generate_chart(tmp_path, {"web": {"image": "nginx"}})
        assert "storeScope" not in values
        assert "reuseStore" not in values

    def test_env_list_format(self, tmp_path):
        _, values = generate_chart(
            tmp_path, {"web": {"image": "nginx", "environment": ["FOO=bar", "TOKEN=abc"]}}
        )
        assert values["services"]["web"]["env"] == {"FOO": "bar"}
        assert values["services"]["web"]["secrets"] == {"TOKEN": "<secret-from-values>"}

    def test_service_names_are_dns_labels(self, tmp_path):
        _, values = generate_chart(
            tmp_path, {"MyCache": {"image": "redis:7", "ports": ["6379"]}}
        )
        assert list(values["services"]) == ["mycache"]
        assert values["services"]["mycache"]["image"] == "redis:7"

    def test_service_name_collision_fails(self, tmp_path):
        with pytest.raises(ValueError, match="collide"):
            generate_chart(
                tmp_path, {"web": {"image": "nginx"}, "WEB": {"image": "nginx:1.25"}}
            )

    def test_unnormalizable_service_name_fails(self, tmp_path):
        with pytest.raises(ValueError, match="cannot be normalized"):
            generate_chart(tmp_path, {"---": {"image": "nginx"}})

    def test_depends_on_uses_chart_keys(self, tmp_path):
        _, values = generate_chart(
            tmp_path,
            {"MyDB": {"image": "postgres:14"}, "App": {"image": "nginx", "depends_on": ["MyDB"]}},
        )
        assert values["services"]["app"]["dependsOn"] == ["mydb"]

    def test_service_aliases_off_by_default(self, tmp_path):
        _, values = generate_chart(tmp_path, {"db": {"image": "postgres:14", "ports": ["5432"]}})
        assert values["serviceAliases"] is False
        assert "alias" not in values["services"]["db"]

    def test_service_aliases_written_when_requested(self, tmp_path):
        _, values = generate_chart(
            tmp_path,
            {"My_DB": {"image": "postgres:14", "ports": ["5432"]}},
            service_aliases=True,
        )
        assert values["serviceAliases"] is True
        assert values["services"]["my-db"]["alias"] == "my-db"

    def test_database_gets_storage_and_mount_paths(self, tmp_path):
        out, values = generate_chart(
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
        assert db["storageVolumes"] == [
            {
                "name": "volume-db-data",
                "mountPath": "/var/lib/postgresql/data",
                "size": "5Gi",
                "readOnly": False,
            }
        ]
        assert (out / "templates" / "statefulset.yaml").exists()

    def test_named_volumes_get_separate_persistent_volume_claims(self, tmp_path):
        _, values = generate_chart(
            tmp_path,
            {
                "app": {
                    "image": "nginx",
                    "replicas": 3,
                    "volumes": [
                        "content:/usr/share/nginx/html",
                        "config:/etc/nginx/conf.d:ro",
                    ],
                }
            },
        )
        app = values["services"]["app"]
        assert app["replicas"] == 3
        assert app["storageVolumes"] == [
            {
                "name": "volume-content",
                "mountPath": "/usr/share/nginx/html",
                "size": "1Gi",
                "readOnly": False,
            },
            {
                "name": "volume-config",
                "mountPath": "/etc/nginx/conf.d",
                "size": "1Gi",
                "readOnly": True,
            },
        ]

    def test_host_bind_mounts_are_warned_and_not_treated_as_pvcs(self, tmp_path, capsys):
        _, values = generate_chart(
            tmp_path,
            {"app": {"image": "nginx", "volumes": ["./config:/etc/nginx/conf.d:ro"]}},
        )
        assert "storageVolumes" not in values["services"]["app"]
        assert "storagePaths" not in values["services"]["app"]
        assert "bind mount" in capsys.readouterr().err.lower()

    def test_compose_networks_are_warned_not_mapped(self, tmp_path, capsys):
        _, values = generate_chart(
            tmp_path, {"app": {"image": "nginx", "networks": ["frontend"]}}
        )
        assert "networks" not in values["services"]["app"]
        assert "network" in capsys.readouterr().err.lower()

    def test_protocol_suffix_port_does_not_crash(self, tmp_path):
        _, values = generate_chart(
            tmp_path, {"web": {"image": "nginx", "ports": ["8080:80/tcp"]}}
        )
        assert values["services"]["web"]["ports"] == [
            {"containerPort": 80, "published": 8080}
        ]

    def test_helpers_template_copied(self, tmp_path):
        out, _ = generate_chart(tmp_path, {"web": {"image": "nginx"}})
        assert (out / "templates" / "_helpers.tpl").exists()

    def test_default_resources_written(self, tmp_path):
        _, values = generate_chart(tmp_path, {"web": {"image": "nginx"}})
        assert values["resources"] == DEFAULT_RESOURCES

    def test_replicas_mapped(self, tmp_path):
        _, values = generate_chart(tmp_path, {"web": {"image": "nginx", "replicas": 3}})
        assert values["services"]["web"]["replicas"] == 3

    def test_command_string_shlex_split(self, tmp_path):
        _, values = generate_chart(
            tmp_path, {"worker": {"image": "python", "command": "python -c 'print(1)'"}}
        )
        assert values["services"]["worker"]["command"] == ["python", "-c", "print(1)"]

    def test_command_list_passthrough(self, tmp_path):
        _, values = generate_chart(
            tmp_path, {"worker": {"image": "python", "args": ["run", "--fast"]}}
        )
        assert values["services"]["worker"]["args"] == ["run", "--fast"]

    def test_deploy_resources_mapped_to_k8s(self, tmp_path):
        _, values = generate_chart(
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
        _, values = generate_chart(
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
        _, values = generate_chart(
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
        _, values = generate_chart(
            tmp_path,
            {"web": {"image": "nginx", "env_file": ".env", "environment": {"FOO": "inline"}}},
        )
        web = values["services"]["web"]
        assert web["env"] == {"FOO": "inline", "BAR": "baz"}
        assert web["secrets"] == {"DB_PASSWORD": "<secret-from-values>"}

    def test_env_file_map_form_missing_optional_ok(self, tmp_path):
        _, values = generate_chart(
            tmp_path,
            {"web": {"image": "nginx", "env_file": [{"path": "absent.env", "required": False}]}},
        )
        assert values["services"]["web"]["env"] == {}

    def test_env_file_list_of_files_later_wins(self, tmp_path):
        (tmp_path / "a.env").write_text("X=1\nY=1")
        (tmp_path / "b.env").write_text("X=2")
        _, values = generate_chart(
            tmp_path, {"web": {"image": "nginx", "env_file": ["a.env", "b.env"]}}
        )
        assert values["services"]["web"]["env"] == {"X": "2", "Y": "1"}

    def test_long_syntax_ports_normalized(self, tmp_path):
        _, values = generate_chart(
            tmp_path,
            {"web": {"image": "nginx", "ports": [{"target": 8080, "published": "80",
                                                  "protocol": "udp"}]}},
        )
        assert values["services"]["web"]["ports"] == [
            {"containerPort": 8080, "published": 80, "protocol": "UDP"}
        ]

    def test_build_only_gets_placeholder_image(self, tmp_path):
        _, values = generate_chart(
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
        _, values = generate_chart(
            tmp_path,
            {"web": {"image": "nginx", "ports": ["8080"]}},
            base_domain="web.example.com",
        )
        assert values["services"]["web"]["ingress"]["rules"][0]["host"] == "web.example.com"

    def test_sensitive_env_flag_flows_into_values(self, tmp_path):
        _, values = generate_chart(
            tmp_path,
            {"web": {"image": "nginx", "environment": {"PUBLIC_KEY": "ssh-rsa AAAA"}}},
            sensitive_env="PUBLIC_KEY",
        )
        assert values["services"]["web"]["secrets"] == {"PUBLIC_KEY": "<secret-from-values>"}

    def test_exec_healthcheck_maps_readiness_only_in_values(self, tmp_path):
        _, values = generate_chart(
            tmp_path,
            {"db": {"image": "postgres:14",
                    "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U app"]}}},
        )
        db = values["services"]["db"]
        assert "readinessProbe" in db
        assert "livenessProbe" not in db

    def test_labels_list_form_ingress_host(self, tmp_path):
        _, values = generate_chart(
            tmp_path,
            {"web": {"image": "nginx", "ports": ["80"],
                     "labels": ["traefik.http.routers.web.rule=Host(`w.com`)"]}},
        )
        assert values["services"]["web"]["ingress"]["rules"][0]["host"] == "w.com"


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
        out, values = generate_chart(tmp_path, {"web": {"image": "nginx"}})
        text = (out / "values.yaml").read_text()
        assert text.startswith("# Generated by helmgen")
        for knob in ("secretProvider", "storeScope", "reuseStore", "resources",
                     "waitOnDependencies", "probePath"):
            assert knob in text
        assert values["services"]["web"]["image"] == "nginx"


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


class TestTopologyValues:
    def test_deps_and_networks_in_values(self, tmp_path):
        _, values = generate_chart(
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
        assert "networks" not in values["services"]["web"]
        assert values["waitOnDependencies"] is False

    def test_shared_storage_paths_reject_multiple_replicas(self, tmp_path):
        compose = write_compose(tmp_path, {"db": {"image": "postgres:14"}})
        overlay = tmp_path / "overlay.yml"
        overlay.write_text(
            "services:\n  db:\n    replicas: 2\n    storage: true\n"
            "    storagePaths: [/var/lib/postgresql/data]\n"
        )
        with pytest.raises(ValueError, match="per-replica persistent volumes"):
            generate_helm_chart(
                compose_path=compose,
                output_dir=tmp_path / "chart",
                secret_provider="internal",
                store_scope="namespace",
                reuse_store=None,
                values_overlay=str(overlay),
            )

    def test_external_secret_store_values_are_configurable(self, tmp_path):
        compose = write_compose(tmp_path, {"app": {"image": "nginx"}})
        overlay = tmp_path / "overlay.yml"
        overlay.write_text(
            "vault:\n"
            "  server: https://vault.example.org\n"
            "  path: apps/\n"
            "  version: v1\n"
            "  auth:\n"
            "    tokenSecretRef:\n"
            "      name: custom-vault-token\n"
            "      key: api-token\n"
        )
        out = tmp_path / "chart"
        generate_helm_chart(
            compose_path=compose,
            output_dir=out,
            secret_provider="externalsecret",
            store_scope="namespace",
            reuse_store=None,
            values_overlay=str(overlay),
        )
        values = yaml.safe_load((out / "values.yaml").read_text())
        assert values["vault"] == {
            "server": "https://vault.example.org",
            "path": "apps/",
            "version": "v1",
            "auth": {"tokenSecretRef": {"name": "custom-vault-token", "key": "api-token"}},
        }
