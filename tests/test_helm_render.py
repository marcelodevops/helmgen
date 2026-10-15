"""Render generated charts with the real helm binary when available."""
import re
import shutil
import subprocess

import pytest
import yaml

from helmgen.chart import generate_helm_chart

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


def build(tmp_path, full_compose, provider="internal", flavor="generic", **kwargs):
    out = tmp_path / f"chart-{provider}-{flavor}"
    generate_helm_chart(
        compose_path=full_compose,
        output_dir=out,
        secret_provider=provider,
        store_scope="namespace",
        reuse_store=None,
        flavor=flavor,
        **kwargs,
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


@pytest.fixture
def net_compose(tmp_path):
    """Ports where host and container port differ, plus a tcp/udp pair on one port."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  payments-gateway-api:
    image: postgres:16
    ports: ["15432:5432", "9090/udp", "9090/tcp", "8080:80"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    depends_on: [db]
    labels:
      traefik.http.routers.payments.rule: "Host(`payments.example.com`)"
  db:
    image: postgres:14
    ports: ["15432:5432"]
    volumes: ["db-data:/var/lib/postgresql/data"]
  worker:
    image: python:3.12-slim
"""
    )
    return compose


def docs(output, kind):
    return [d for d in yaml.safe_load_all(output) if d and d["kind"] == kind]


def services_by_name(output):
    return {d["metadata"]["name"]: d for d in docs(output, "Service")}


def test_service_port_names_are_rfc6335_valid(tmp_path, net_compose):
    output = render_ok(build(tmp_path, net_compose))
    pattern = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
    for name, svc in services_by_name(output).items():
        names = [p["name"] for p in svc["spec"]["ports"]]
        assert len(names) == len(set(names)), f"{name} has duplicate port names"
        for port_name in names:
            assert len(port_name) <= 15, f"{name} port name {port_name!r} is too long"
            assert pattern.match(port_name), f"{name} port name {port_name!r} is invalid"
        protocols = [(p["port"], p["protocol"]) for p in svc["spec"]["ports"]]
        assert len(protocols) == len(set(protocols)), f"{name} has duplicate ports"


def test_service_ports_are_container_ports_not_published(tmp_path, net_compose):
    output = render_ok(build(tmp_path, net_compose))
    ports = {
        (p["port"], p["targetPort"])
        for p in services_by_name(output)["test-db"]["spec"]["ports"]
    }
    assert ports == {(5432, 5432)}


def test_ingress_backend_port_exists_on_referenced_service(tmp_path, net_compose):
    output = render_ok(build(tmp_path, net_compose))
    services = services_by_name(output)
    ingress_docs = docs(output, "Ingress")
    assert ingress_docs
    for ingress in ingress_docs:
        for rule in ingress["spec"]["rules"]:
            for path in rule["http"]["paths"]:
                backend = path["backend"]["service"]
                assert backend["name"] in services, f"{backend['name']} was not rendered"
                served = {p["port"] for p in services[backend["name"]]["spec"]["ports"]}
                assert backend["port"]["number"] in served


def test_statefulset_service_name_is_headless(tmp_path, net_compose):
    output = render_ok(build(tmp_path, net_compose))
    services = services_by_name(output)
    for sts in docs(output, "StatefulSet"):
        governing = sts["spec"]["serviceName"]
        assert governing in services, f"{governing} was not rendered"
        svc = services[governing]
        assert svc["spec"]["clusterIP"] == "None"
        pod_labels = sts["spec"]["template"]["metadata"]["labels"]
        assert set(svc["spec"]["selector"].items()) <= set(pod_labels.items())
        assert {p["port"] for p in svc["spec"]["ports"]} == {
            p["containerPort"] for p in sts["spec"]["template"]["spec"]["containers"][0]["ports"]
        }


def test_wait_for_init_container_targets_the_service_port(tmp_path, net_compose):
    output = render_ok(build(tmp_path, net_compose), "--set", "waitOnDependencies=true")
    services = services_by_name(output)
    checked = 0
    for kind in ("Deployment", "StatefulSet"):
        for workload in docs(output, kind):
            for init in workload["spec"]["template"]["spec"].get("initContainers", []):
                match = re.search(
                    r"nc -z\s+(?:-w\s+\d+\s+)?(\S+)\s+(\d+)", " ".join(init["command"])
                )
                assert match, init["command"]
                host, port = match.group(1), int(match.group(2))
                assert host in services, f"{host} was not rendered"
                assert port in {p["port"] for p in services[host]["spec"]["ports"]}
                checked += 1
    assert checked, "no wait-for init containers rendered"


def test_service_aliases_opt_in(tmp_path, net_compose):
    plain = services_by_name(render_ok(build(tmp_path, net_compose)))
    assert "db" not in plain
    assert not any("helmgen.io/alias-for" in svc["metadata"]["labels"] for svc in plain.values())

    aliased = services_by_name(render_ok(build(tmp_path, net_compose, service_aliases=True)))
    assert "db" in aliased
    assert aliased["db"]["spec"]["selector"] == aliased["test-db"]["spec"]["selector"]
    assert aliased["db"]["spec"]["ports"] == aliased["test-db"]["spec"]["ports"]
    assert aliased["db"]["metadata"]["labels"]["helmgen.io/alias-for"] == "test-db"


def test_service_aliases_normalize_uppercase_names(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  My_DB:
    image: postgres:14
    ports: ["5432:5432"]
"""
    )
    chart = tmp_path / "chart"
    generate_helm_chart(
        compose_path=compose,
        output_dir=chart,
        secret_provider="internal",
        store_scope="namespace",
        reuse_store=None,
        service_aliases=True,
    )
    lint_ok(chart)
    aliased = services_by_name(render_ok(chart))
    assert "my-db" in aliased
    assert aliased["my-db"]["spec"]["selector"] == aliased["test-my-db"]["spec"]["selector"]
