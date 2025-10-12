#!/usr/bin/env python3
import os
import re
import sys
import yaml
import argparse
from pathlib import Path
from shutil import copyfile

# -------------------------------
# Utility helpers
# -------------------------------

SENSITIVE_KEYS = [
    "PASSWORD", "PASS", "SECRET", "KEY", "TOKEN", "CREDENTIAL"
]

BASE_HELM_TEMPLATES = [
    "deployment.yaml",
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

def detect_ingress(service):
    ingress = None
    ports = service.get("ports", [])
    labels = service.get("labels", {})
    host = None
    tls = False

    for p in ports:
        port_num = None
        if isinstance(p, str):
            parts = p.split(":")
            try:
                port_num = int(parts[-1])
            except ValueError:
                continue
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
            host_match = re.search(r"Host\(`?([^)]+)`?\)", val)
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
        ingress.setdefault("annotations", {
            "kubernetes.io/ingress.class": "nginx"
        })

    return ingress

# -------------------------------
# Chart generation
# -------------------------------

def generate_helm_chart(compose_path, output_dir, secret_provider, store_scope, reuse_store):
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
    values = {"services": {}, "secretProvider": secret_provider}

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
                parts = p.split(":")
                port_map = {"containerPort": int(parts[-1])}
                if len(parts) > 1 and parts[0].isdigit():
                    port_map["published"] = int(parts[0])
                ports.append(port_map)
            elif isinstance(p, dict):
                ports.append(p)
        if ports:
            service_data["ports"] = ports

        # Volumes
        if "volumes" in svc:
            service_data["storage"] = True

        # Ingress
        ingress = detect_ingress(svc)
        if ingress:
            service_data["ingress"] = ingress

        values["services"][name] = service_data

    # Save Chart.yaml and values.yaml
    with open(output_dir / "Chart.yaml", "w") as f:
        yaml.safe_dump(chart_yaml, f, sort_keys=False)
    with open(output_dir / "values.yaml", "w") as f:
        yaml.safe_dump(values, f, sort_keys=False)

    # Copy templates
    local_tpl_dir = Path("helm_templates")
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
    )

if __name__ == "__main__":
    main()
