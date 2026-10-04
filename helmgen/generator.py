#!/usr/bin/env python3
import re
import shlex
import sys
import yaml
import argparse
from pathlib import Path
import importlib.resources as pkg_resources
from shutil import copyfile

# -------------------------------------------------------------------
# Locate Helm templates robustly (works in dev mode and installed pkg)
# -------------------------------------------------------------------
def get_template_dir() -> Path:
    """
    Locate the helm_templates directory whether running from source
    or from an installed package.
    """
    # Try to resolve from package resources (works after install)
    try:
        return Path(pkg_resources.files("helmgen") / "helm_templates")
    except Exception:
        # Fallback for local dev execution
        return Path(__file__).parent / "helm_templates"

TEMPLATE_DIR = get_template_dir()

# -------------------------------
# Utility helpers
# -------------------------------

SENSITIVE_KEYS = [
    "PASSWORD", "PASS", "SECRET", "KEY", "TOKEN", "CREDENTIAL"
]

BASE_HELM_TEMPLATES = [
    "_helpers.tpl",
    "deployment.yaml",
    "statefulset.yaml",
    "service.yaml",
    "pvc.yaml",
    "ingress.yaml",
    "secrets.yaml",
    "externalsecret.yaml",
    "secretstore.yaml",
]

DEFAULT_STORAGE_SIZES = {
    "postgres": "5Gi",
    "mysql": "5Gi",
    "mariadb": "5Gi",
    "mongodb": "5Gi",
    "redis": "1Gi",
}

DEFAULT_RESOURCES = {
    "requests": {"cpu": "10m", "memory": "128Mi"},
    "limits": {"cpu": "1", "memory": "512Mi"},
}

# -------------------------------
# Core logic
# -------------------------------

def is_database(image):
    db_keywords = ["postgres", "mysql", "mariadb", "mongodb", "redis"]
    return any(k in image.lower() for k in db_keywords)

def detect_sensitive_env(env_dict):
    sensitive = {}
    normal = {}
    for k, v in env_dict.items():
        if any(s in k.upper() for s in SENSITIVE_KEYS):
            sensitive[k] = "<secret-from-values>"
        else:
            normal[k] = v
    return normal, sensitive

def parse_port_string(p):
    """Parse a compose port string like '8080:80/tcp' into (published, container) ints."""
    parts = str(p).split(":")
    try:
        container = int(parts[-1].split("/")[0])
    except ValueError:
        return None, None
    published = None
    if len(parts) > 1 and parts[-2].isdigit():
        published = int(parts[-2])
    return published, container

def compose_resources_to_k8s(resources):
    """Convert compose deploy.resources.{limits,requests} to K8s quantities."""
    out = {}
    for role in ("requests", "limits"):
        vals = resources.get(role) or {}
        mapped = {}
        if "cpus" in vals:
            mapped["cpu"] = f"{int(float(vals['cpus']) * 1000)}m"
        elif "cpu" in vals:
            mapped["cpu"] = str(vals["cpu"])
        if "memory" in vals:
            mapped["memory"] = str(vals["memory"])
        if mapped:
            out[role] = mapped
    return out

def parse_compose_duration(value):
    """Convert a compose duration like '30s', '500ms' or '1m' to whole seconds."""
    m = re.match(r"^(\d+(?:\.\d+)?)(ms|s|m|h)?$", str(value).strip())
    if not m:
        return None
    num, unit = float(m.group(1)), m.group(2) or "s"
    seconds = num * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return max(int(seconds), 1)

def detect_probes(service):
    """Build a Kubernetes probe from a compose healthcheck, or None."""
    hc = service.get("healthcheck") or {}
    if hc.get("disable"):
        return None
    test = hc.get("test")
    if not test:
        return None

    if isinstance(test, list):
        cmd = [str(x) for x in test]
        if cmd and cmd[0].upper() in ("CMD", "CMD-SHELL", "NONE"):
            cmd = cmd[1:]
        if len(cmd) == 1:
            cmd = shlex.split(cmd[0])
    else:
        cmd = shlex.split(str(test))
    if not cmd:
        return None

    url = next((a for a in cmd if a.lower().startswith(("http://", "https://"))), None)
    if url:
        m = re.match(r"https?://[^/:]+(?::(\d+))?(/[^?#]*)?", url, re.I)
        if m:
            default_port = 443 if url.lower().startswith("https") else 80
            probe = {"httpGet": {
                "path": m.group(2) or "/",
                "port": int(m.group(1)) if m.group(1) else default_port,
            }}
        else:
            probe = {"exec": {"command": cmd}}
    else:
        probe = {"exec": {"command": cmd}}

    def duration(field, default=None):
        value = hc.get(field, default)
        return parse_compose_duration(value) if value is not None else None

    timeout = duration("timeout")
    interval = duration("interval")
    start_period = duration("start_period")
    if timeout:
        probe["timeoutSeconds"] = timeout
    if interval:
        probe["periodSeconds"] = interval
    if hc.get("retries") is not None:
        probe["failureThreshold"] = int(hc["retries"])
    if start_period and start_period > 1:
        probe["initialDelaySeconds"] = start_period
    return probe

def detect_ingress(service, ingress_class="nginx"):
    ingress = None
    ports = service.get("ports", [])
    labels = service.get("labels", {})
    host = None
    tls = False

    for p in ports:
        port_num = None
        if isinstance(p, str):
            _, port_num = parse_port_string(p)
        elif isinstance(p, dict):
            port_num = p.get("target") or p.get("published") or p.get("containerPort")

        if port_num and int(port_num) in [80, 443, 8080]:
            ingress = ingress or {}
            ingress.setdefault("rules", [])
            ingress["rules"].append({
                "host": "example.local",
                "path": "/",
                "port": port_num,
            })
            if int(port_num) == 443:
                tls = True

    for key, val in labels.items():
        if "Host:" in val:
            host_match = re.search(r"Host:([^\s,;]+)", val)
            if host_match:
                host = host_match.group(1)
        elif "Host(" in val:
            host_match = re.search(r"Host\(`?([^)`]+)`?\)", val)
            if host_match:
                host = host_match.group(1)
        elif key in ["ingress.host", "ingress.domain"]:
            host = val

    if host:
        ingress = ingress or {}
        if "rules" not in ingress or not ingress["rules"]:
            ingress["rules"] = [{"host": host, "path": "/", "port": 80}]
        else:
            for rule in ingress["rules"]:
                rule["host"] = host

    if tls or any("tls" in k.lower() for k in labels):
        ingress.setdefault("tls", [{
            "hosts": [host or "example.local"],
            "secretName": f"{host or 'example'}-tls"
        }])

    if ingress:
        ingress.setdefault("className", ingress_class)

    return ingress

# -------------------------------
# Chart generation
# -------------------------------

def generate_helm_chart(compose_path, output_dir, secret_provider, store_scope,
                        reuse_store, ingress_class="nginx"):
    with open(compose_path) as f:
        compose = yaml.safe_load(f)

    output_dir.mkdir(parents=True, exist_ok=True)
    templates_dir = output_dir / "templates"
    templates_dir.mkdir(exist_ok=True)

    # Chart.yaml
    chart_yaml = {
        "apiVersion": "v2",
        "name": output_dir.name,
        "version": "0.1.0",
        "appVersion": "1.0.0",
        "description": f"Helm chart auto-generated from {compose_path.name}"
    }

    # Build values.yaml
    values = {
        "services": {},
        "secretProvider": secret_provider,
        "resources": DEFAULT_RESOURCES,
    }
    if secret_provider == "externalsecret":
        values["storeScope"] = store_scope
        if reuse_store:
            values["reuseStore"] = reuse_store

    for name, svc in compose.get("services", {}).items():
        image = svc.get("image", "unknown")
        env_raw = svc.get("environment", {})
        env = {}
        if isinstance(env_raw, list):
            for item in env_raw:
                k, v = item.split("=", 1) if "=" in item else (item, "")
                env[k] = v
        else:
            env = env_raw

        normal_env, sensitive_env = detect_sensitive_env(env)

        service_data = {
            "image": image,
            "env": normal_env,
            "secrets": sensitive_env,
        }

        # Database detection → StatefulSet + storage
        if is_database(image):
            service_data["storage"] = True
            service_data["storageSize"] = DEFAULT_STORAGE_SIZES.get(
                next((db for db in DEFAULT_STORAGE_SIZES if db in image.lower()), "postgres"),
                "5Gi"
            )

        # Ports
        ports = []
        for p in svc.get("ports", []):
            if isinstance(p, str):
                published, container = parse_port_string(p)
                if container is None:
                    continue
                port_map = {"containerPort": container}
                if published is not None:
                    port_map["published"] = published
                ports.append(port_map)
            elif isinstance(p, dict):
                ports.append(p)
        if ports:
            service_data["ports"] = ports

        # Volumes
        if "volumes" in svc:
            service_data["storage"] = True
            mount_paths = []
            for v in svc["volumes"]:
                if isinstance(v, str):
                    parts = v.split(":")
                    if len(parts) >= 2 and parts[1] not in mount_paths:
                        mount_paths.append(parts[1])
            if mount_paths:
                service_data["storagePaths"] = mount_paths

        # Replicas / command / args
        if "replicas" in svc:
            service_data["replicas"] = int(svc["replicas"])
        for field in ("command", "args"):
            val = svc.get(field)
            if val:
                service_data[field] = (
                    shlex.split(val) if isinstance(val, str) else [str(x) for x in val]
                )

        # Resources from deploy.resources
        deploy = svc.get("deploy") or {}
        res = compose_resources_to_k8s(deploy.get("resources") or {})
        if res:
            service_data["resources"] = res

        # healthcheck → liveness/readiness probes
        probe = detect_probes(svc)
        if probe:
            service_data["livenessProbe"] = dict(probe)
            service_data["readinessProbe"] = dict(probe)

        # Ingress
        ingress = detect_ingress(svc, ingress_class)
        if ingress:
            service_data["ingress"] = ingress

        values["services"][name] = service_data

    # Save Chart.yaml and values.yaml
    with open(output_dir / "Chart.yaml", "w") as f:
        yaml.safe_dump(chart_yaml, f, sort_keys=False)
    with open(output_dir / "values.yaml", "w") as f:
        yaml.safe_dump(values, f, sort_keys=False)

    # Copy templates
    local_tpl_dir = Path(TEMPLATE_DIR)
    for tpl in BASE_HELM_TEMPLATES:
        src = local_tpl_dir / tpl
        dest = templates_dir / tpl
        if not src.exists():
            print(f"⚠️  Missing template: {src}")
            continue
        copyfile(src, dest)

    print(f"✅ Helm chart generated at {output_dir.resolve()}")

# -------------------------------
# CLI entry point
# -------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate a Helm chart from Docker Compose")
    parser.add_argument("compose_file", help="Path to docker-compose.yml")
    parser.add_argument("--output", "-o", default="./generated-chart", help="Output directory")
    parser.add_argument("--secret-provider", "-s", choices=["internal", "externalsecret"],
                        default="internal", help="Secret provider")
    parser.add_argument("--store-scope", choices=["namespace", "cluster"],
                        default="namespace", help="SecretStore scope")
    parser.add_argument("--reuse-store", default=None,
                        help="Reuse an existing SecretStore or ClusterSecretStore")
    parser.add_argument("--ingress-class", default="nginx",
                        help="ingressClassName for generated Ingress resources")
    args = parser.parse_args()

    compose_file = Path(args.compose_file)
    if not compose_file.exists():
        sys.exit(f"File not found: {compose_file}")

    generate_helm_chart(
        compose_path=compose_file,
        output_dir=Path(args.output),
        secret_provider=args.secret_provider,
        store_scope=args.store_scope,
        reuse_store=args.reuse_store,
        ingress_class=args.ingress_class,
    )

if __name__ == "__main__":
    main()
