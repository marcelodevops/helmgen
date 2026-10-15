"""Compose document to Helm chart directory: values.yaml, Chart.yaml, templates."""
import importlib.resources as pkg_resources
import shlex
from pathlib import Path
from shutil import copyfile

import yaml

from .compose import (
    compose_resources_to_k8s,
    load_env_files,
    normalize_depends_on,
    normalize_networks,
    normalize_ports,
    normalize_volume_mounts,
    sanitize_service_name,
    warn,
)
from .flavors import apply_flavor, detect_flavor
from .heuristics import detect_sensitive_env, image_repo, is_database, map_healthcheck
from .ingress import detect_ingress
from .presets import (
    BASE_HELM_TEMPLATES,
    DATABASE_REPOS,
    DEFAULT_RESOURCES,
    FLAVOR_ALIASES,
    SUPPORTED_FLAVORS,
    VALUES_DOC,
)

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


def deep_merge(base, overlay):
    """Recursively merge overlay into base (overlay wins); non-dicts replace."""
    out = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def generate_helm_chart(compose_path, output_dir, secret_provider, store_scope,
                        reuse_store, ingress_class="nginx", values_overlay=None,
                        flavor="generic", chart_version="0.1.0", app_version="1.0.0",
                        chart_name=None, base_domain=None, sensitive_env=None,
                        service_aliases=False):
    canonical_flavor = FLAVOR_ALIASES.get(str(flavor).lower(), str(flavor).lower())
    if canonical_flavor not in SUPPORTED_FLAVORS:
        raise ValueError(
            f"Unsupported flavor '{flavor}'. Choose from: {', '.join(SUPPORTED_FLAVORS)}"
        )

    with open(compose_path) as f:
        compose = yaml.safe_load(f)

    compose = compose if isinstance(compose, dict) else {}
    base_dir = Path(compose_path).parent
    extra_sensitive = (
        sensitive_env.split(",") if isinstance(sensitive_env, str) else (sensitive_env or [])
    )

    for key in ("configs", "secrets"):
        if compose.get(key):
            warn(
                f"top-level '{key}' is not supported by helmgen; mount files or use "
                "volumes/ExternalSecrets in the generated chart instead"
            )
    if any(isinstance(svc, dict) and svc.get("extends") for svc in compose.get("services", {}).values()):
        warn("service 'extends' is not resolved by helmgen; check the merged result manually")

    output_dir.mkdir(parents=True, exist_ok=True)
    templates_dir = output_dir / "templates"
    templates_dir.mkdir(exist_ok=True)

    # Chart.yaml
    chart_yaml = {
        "apiVersion": "v2",
        "name": chart_name or output_dir.name,
        "version": chart_version,
        "appVersion": app_version,
        "description": f"Helm chart auto-generated from {compose_path.name}"
    }

    # Build values.yaml
    values = {
        "services": {},
        "secretProvider": secret_provider,
        "resources": DEFAULT_RESOURCES,
        "waitOnDependencies": False,
        "serviceAliases": bool(service_aliases),
    }
    if canonical_flavor != "generic":
        values["flavor"] = canonical_flavor
    if secret_provider == "externalsecret":
        values["storeScope"] = store_scope
        if reuse_store:
            values["reuseStore"] = reuse_store
        else:
            warn(
                "generated Vault SecretStore uses placeholder settings; configure the "
                "vault values before deployment"
            )
            values["vault"] = {
                "server": "https://vault.example.com",
                "path": "secret/",
                "version": "v2",
                "auth": {"tokenSecretRef": {"name": "vault-auth", "key": "token"}},
            }

    build_only = []
    for name, svc in compose.get("services", {}).items():
        # Kubernetes object names must be lowercase DNS labels; Compose allows
        # uppercase, so the chart keys resources by the normalized name.
        svc_key = sanitize_service_name(name)
        if svc_key is None:
            raise ValueError(
                f"service {name!r} cannot be normalized to a valid Kubernetes name"
            )
        if svc_key != name:
            warn(f"service {name!r} is generated as {svc_key!r} (Kubernetes names are lowercase)")
        if svc_key in values["services"]:
            raise ValueError(
                f"two services collide on the Kubernetes name {svc_key!r}; "
                "rename one in the compose file"
            )

        image = svc.get("image") or ""
        if not image:
            if svc.get("build"):
                image = f"{svc_key}:local"
                build_only.append(svc_key)
            else:
                image = "unknown"

        env = {}
        env.update(load_env_files(svc.get("env_file"), base_dir))
        env_raw = svc.get("environment", {})
        if isinstance(env_raw, list):
            for item in env_raw:
                k, v = item.split("=", 1) if "=" in item else (item, "")
                env[k] = v
        elif env_raw:
            env.update(env_raw)

        normal_env, sensitive_env_map = detect_sensitive_env(env, extra_sensitive)

        service_data = {
            "image": image,
            "env": normal_env,
            "secrets": sensitive_env_map,
        }

        # Database detection → StatefulSet + storage
        if is_database(image):
            service_data["storage"] = True
            service_data["storageSize"] = DATABASE_REPOS.get(image_repo(image), "5Gi")

        # Ports
        ports = normalize_ports(svc.get("ports"))
        if ports:
            service_data["ports"] = ports

        # Legacy DNS alias (db:5432 must resolve without rewriting env values)
        if service_aliases:
            service_data["alias"] = svc_key

        # Named Compose volumes become per-replica persistent volume claims.
        if "volumes" in svc:
            storage_size = service_data.get("storageSize", "1Gi")
            storage_volumes = normalize_volume_mounts(svc["volumes"], storage_size)
            if storage_volumes:
                service_data["storage"] = True
                service_data["storageVolumes"] = storage_volumes
                claims = {}
                for volume in storage_volumes:
                    claims.setdefault(volume["name"], {
                        "name": volume["name"],
                        "size": volume["size"],
                    })
                service_data["storageClaims"] = list(claims.values())

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

        # healthcheck → liveness/readiness/startup probes
        service_data.update(map_healthcheck(svc))

        # Topology metadata from compose
        deps = normalize_depends_on(svc.get("depends_on"))
        if deps:
            # dependsOn is consumed by the wait-for templates, which look the
            # dependency up by key in values.services, so normalize names the
            # same way the services themselves are keyed.
            service_data["dependsOn"] = [sanitize_service_name(d) or str(d) for d in deps]
        nets = normalize_networks(svc.get("networks"))
        if nets:
            warn(
                f"service {name!r} uses Compose networks {', '.join(nets)}; "
                "network isolation is not mapped to Kubernetes"
            )

        # Ingress
        ingress = detect_ingress(svc, ingress_class, base_domain, ports=ports)
        if ingress:
            service_data["ingress"] = ingress

        # Template flavor detection and presets
        svc_flavor = detect_flavor(svc, default_flavor=canonical_flavor)
        if svc_flavor != "generic":
            apply_flavor(service_data, svc_flavor)

        values["services"][svc_key] = service_data

    if values_overlay:
        overlay = yaml.safe_load(Path(values_overlay).read_text()) or {}
        if not isinstance(overlay, dict):
            raise ValueError("values overlay must be a YAML mapping")
        values = deep_merge(values, overlay)

    for name, service in values.get("services", {}).items():
        if (
            service.get("storage")
            and service.get("storagePaths")
            and not service.get("storageVolumes")
            and int(service.get("replicas", 1)) > 1
        ):
            raise ValueError(
                f"service {name!r} mounts one shared PVC across multiple replicas; "
                "use per-replica persistent volumes instead"
            )

    if build_only:
        warn(
            "service(s) use `build` with no `image`; wrote placeholder image(s) "
            f"{', '.join(f'{n}:local' for n in build_only)} — set the real image in values.yaml "
            "or an overlay after building and pushing"
        )

    # Save Chart.yaml and values.yaml
    with open(output_dir / "Chart.yaml", "w") as f:
        yaml.safe_dump(chart_yaml, f, sort_keys=False)
    with open(output_dir / "values.yaml", "w") as f:
        f.write(VALUES_DOC.format(compose=compose_path.name))
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

