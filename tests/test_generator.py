import yaml
import pytest

from helmgen.generator import (
    DEFAULT_RESOURCES,
    compose_resources_to_k8s,
    detect_ingress,
    detect_sensitive_env,
    generate_helm_chart,
    is_database,
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


class TestComposeResources:
    def test_cpus_converted_to_millicores(self):
        assert compose_resources_to_k8s({"limits": {"cpus": "2.0"}}) == {
            "limits": {"cpu": "2000m"}
        }

    def test_empty(self):
        assert compose_resources_to_k8s({}) == {}

