"""Tests for workload flavor presets (helmgen.flavors)."""

import yaml
import pytest
import subprocess
import sys

from helmgen.presets import FLAVOR_RESOURCES, SUPPORTED_FLAVORS
from helmgen.flavors import detect_flavor
from conftest import generate_chart, write_compose


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
        _, values = generate_chart(
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
        _, values = generate_chart(
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
        _, values = generate_chart(
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
        _, values = generate_chart(
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
        with pytest.raises(ValueError, match="Unsupported flavor"):
            generate_chart(tmp_path, {"web": {"image": "nginx"}}, flavor="unknown-flavor")

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
