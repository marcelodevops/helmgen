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


def build(tmp_path, full_compose, provider="internal"):
    out = tmp_path / f"chart-{provider}"
    generate_helm_chart(
        compose_path=full_compose,
        output_dir=out,
        secret_provider=provider,
        store_scope="namespace",
        reuse_store=None,
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
    output = render_ok(chart)
    kinds = [d["kind"] for d in yaml.safe_load_all(output) if d]
    assert "ExternalSecret" in kinds
    assert "SecretStore" in kinds  # no reuseStore set
    assert "Secret" not in kinds  # internal secrets suppressed


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
