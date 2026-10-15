"""Render generated charts with the real helm binary when available."""
import shutil
import subprocess

import pytest
import yaml

from helmgen.generator import generate_helm_chart

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm binary not available"
)


def run_helm(*args):
    result = subprocess.run(["helm", *args], capture_output=True, text=True)
    return result


def render_ok(chart_dir, *extra_args):
    result = run_helm("template", "test", str(chart_dir), "--skip-crds", *extra_args)
    assert result.returncode == 0, result.stderr
    return result.stdout


def lint_ok(chart_dir):
    result = run_helm("lint", str(chart_dir))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def full_compose(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  web:
    image: nginx:alpine
    ports: ["8080:80/tcp"]
    environment: {APP_ENV: prod, SECRET_KEY: x}
    labels: {rule: "Host(`web.example.com`)"}
    depends_on: [db]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/healthz"]
      interval: 15s
      timeout: 3s
      retries: 5
  worker:
    image: python:3.12-slim
  db:
    image: postgres:14
    environment: {POSTGRES_PASSWORD: secret}
    volumes: ["db-data:/var/lib/postgresql/data"]
    ports: ["5432:5432"]
"""
    )
    return compose


def build(tmp_path, full_compose, provider="internal", flavor="generic"):
    out = tmp_path / f"chart-{provider}-{flavor}"
    generate_helm_chart(
        compose_path=full_compose,
        output_dir=out,
        secret_provider=provider,
        store_scope="namespace",
        reuse_store=None,
        flavor=flavor,
    )
    return out


def test_internal_chart_lints_and_renders(tmp_path, full_compose):
    chart = build(tmp_path, full_compose)
    lint_ok(chart)
    output = render_ok(chart)
    kinds = [d["kind"] for d in yaml.safe_load_all(output) if d]
    assert kinds.count("Deployment") == 2  # web + worker
    assert kinds.count("StatefulSet") == 1  # db
    assert "Service" in kinds and "Ingress" in kinds and "Secret" in kinds


def test_probe_and_labels_render(tmp_path, full_compose):
    output = render_ok(build(tmp_path, full_compose))
    docs = [d for d in yaml.safe_load_all(output) if d and d["kind"] == "Deployment"]
    web = next(d for d in docs if d["metadata"]["name"].endswith("-web"))
    container = web["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["readinessProbe"]["httpGet"]["port"] == 8080
    assert web["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "Helm"


def test_externalsecret_chart_lints_and_renders(tmp_path, full_compose):
    chart = build(tmp_path, full_compose, provider="externalsecret")
    lint_ok(chart)
    output = render_ok(
        chart,
        "--set", "vault.server=https://vault.example.org",
        "--set", "vault.path=apps/",
        "--set", "vault.version=v1",
        "--set", "vault.auth.tokenSecretRef.name=custom-vault-token",
        "--set", "vault.auth.tokenSecretRef.key=api-token",
    )
    kinds = [d["kind"] for d in yaml.safe_load_all(output) if d]
    assert "ExternalSecret" in kinds
    assert "SecretStore" in kinds  # no reuseStore set
    assert "Secret" not in kinds  # internal secrets suppressed
    store = next(d for d in yaml.safe_load_all(output) if d and d["kind"] == "SecretStore")
    vault = store["spec"]["provider"]["vault"]
    assert vault["server"] == "https://vault.example.org"
    assert vault["path"] == "apps/"
    assert vault["version"] == "v1"
    assert vault["auth"]["tokenSecretRef"] == {"name": "custom-vault-token", "key": "api-token"}


def test_named_volumes_render_per_replica_claim_templates(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  app:
    image: nginx:alpine
    replicas: 3
    volumes:
      - content:/usr/share/nginx/html
      - config:/etc/nginx/conf.d:ro
"""
    )
    chart = tmp_path / "chart"
    generate_helm_chart(
        compose_path=compose,
        output_dir=chart,
        secret_provider="internal",
        store_scope="namespace",
        reuse_store=None,
    )
    lint_ok(chart)
    output = render_ok(chart)
    stateful = next(d for d in yaml.safe_load_all(output) if d and d["kind"] == "StatefulSet")
    assert stateful["spec"]["replicas"] == 3
    claims = stateful["spec"]["volumeClaimTemplates"]
    assert [claim["metadata"]["name"] for claim in claims] == [
        "volume-content",
        "volume-config",
    ]
    mounts = stateful["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert [(mount["name"], mount["mountPath"], mount["readOnly"]) for mount in mounts] == [
        ("volume-content", "/usr/share/nginx/html", False),
        ("volume-config", "/etc/nginx/conf.d", True),
    ]


def test_ingress_renders_all_paths_for_each_host(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  app:
    image: nginx:alpine
    ports: ["80"]
    labels:
      traefik.http.routers.app.rule: "Host(`app.example.com`) && (PathPrefix(`/api`) || Path(`/health`))"
"""
    )
    chart = tmp_path / "chart"
    generate_helm_chart(
        compose_path=compose,
        output_dir=chart,
        secret_provider="internal",
        store_scope="namespace",
        reuse_store=None,
    )
    output = render_ok(chart)
    ingress = next(d for d in yaml.safe_load_all(output) if d and d["kind"] == "Ingress")
    rendered_paths = [
        (rule["host"], path["path"])
        for rule in ingress["spec"]["rules"]
        for path in rule["http"]["paths"]
    ]
    assert rendered_paths == [
        ("app.example.com", "/api"),
        ("app.example.com", "/health"),
    ]


def _web_deployment(output):
    for d in yaml.safe_load_all(output):
        if d and d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-web"):
            return d
    raise AssertionError("web Deployment not rendered")


def test_no_init_containers_by_default(tmp_path, full_compose):
    web = _web_deployment(render_ok(build(tmp_path, full_compose)))
    assert "initContainers" not in web["spec"]["template"]["spec"]


def test_wait_on_dependencies_renders_init_container(tmp_path, full_compose):
    chart = build(tmp_path, full_compose)
    output = render_ok(chart, "--set", "waitOnDependencies=true")
    web = _web_deployment(output)
    init = web["spec"]["template"]["spec"]["initContainers"]
    assert init[0]["name"] == "wait-for-db"
    assert "test-db 5432" in init[0]["command"][2]


def test_spring_boot_flavor_lints_and_renders(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  app:
    image: springboot-service:latest
    ports: ["8080:8080"]
"""
    )
    chart = build(tmp_path, compose, flavor="spring-boot")
    lint_ok(chart)
    output = render_ok(chart)
    docs = [d for d in yaml.safe_load_all(output) if d and d["kind"] == "Deployment"]
    assert len(docs) == 1
    app = docs[0]
    container = app["spec"]["template"]["spec"]["containers"][0]
    assert container["startupProbe"]["httpGet"]["path"] == "/actuator/health/liveness"
    assert container["livenessProbe"]["httpGet"]["path"] == "/actuator/health/liveness"
    assert container["readinessProbe"]["httpGet"]["path"] == "/actuator/health/readiness"


def test_payara_flavor_lints_and_renders(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  app:
    image: payara/micro:6.2023.4
    ports: ["8080:8080", "4848:4848"]
"""
    )
    chart = build(tmp_path, compose, flavor="payara")
    lint_ok(chart)
    output = render_ok(chart)
    docs = [d for d in yaml.safe_load_all(output) if d and d["kind"] == "Deployment"]
    assert len(docs) == 1
    app = docs[0]
    container = app["spec"]["template"]["spec"]["containers"][0]
    assert container["startupProbe"]["httpGet"]["path"] == "/health/live"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert container["resources"]["limits"]["memory"] == "2Gi"
