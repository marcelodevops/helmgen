"""Ingress detection from compose labels, ports and the base-domain fallback."""
import re

from ..compose import normalize_labels, normalize_ports
from ..presets import WEB_PORTS
from .traefik import _rule_hostsets, _rule_paths, _traefik_field

def detect_ingress(service, ingress_class="nginx", base_domain=None, ports=None):
    labels = normalize_labels(service.get("labels"))
    tls = False

    host_sets = []
    paths = []
    for key, val in labels.items():
        val = str(val)
        if re.search(r"Host\s*[(:]", val, re.I):
            host_sets.extend(_rule_hostsets(val))
            if re.search(r"(?:^|[\s(&|])tls(?=$|[\s)&|])", val.lower()):
                tls = True
        if str(key).lower().startswith("traefik.https.routers."):
            tls = True  # router attached to the secure entrypoint
        field = _traefik_field(key)
        if field and field.split(".")[0] == "tls" and val.strip().lower() != "false":
            tls = True
        paths.extend(_rule_paths(val))
    paths = list(dict.fromkeys(paths)) or ["/"]
    if base_domain:
        tls = True

    # Rules target the Service port, which is the container port (see service.yaml),
    # so consume the already-normalized mapping list instead of re-parsing compose.
    normalized = ports if ports is not None else normalize_ports(service.get("ports"))
    container_ports = []
    for p in normalized or []:
        port_num = int(p["containerPort"])
        if port_num not in container_ports:
            container_ports.append(port_num)
    if not container_ports:
        # No container port means no Service is rendered, so nothing to route to.
        return None
    if 443 in container_ports:
        tls = True

    # A host rule with no port hint routes to the web port if the service exposes
    # one, otherwise to its first port.
    first_port = next((p for p in container_ports if p in WEB_PORTS), None)
    first_port = first_port or container_ports[0]
    rules = []

    configured_host = next(
        (labels[k] for k in ("ingress.host", "ingress.domain") if labels.get(k)), None
    )
    paths = paths or ["/"]
    if configured_host:
        for path in paths:
            rules.append({"host": configured_host, "path": path, "port": first_port})
    elif host_sets:
        for hosts in host_sets:
            for h in hosts:
                for path in paths:
                    rules.append({"host": h, "path": path, "port": first_port})
    elif base_domain:
        web_ports = [p for p in container_ports if p in WEB_PORTS]
        for port_num in web_ports:
            for path in paths:
                rules.append({"host": base_domain, "path": path, "port": port_num})
    else:
        for port_num in container_ports:
            if port_num in WEB_PORTS:
                for path in paths:
                    rules.append({"host": "example.local", "path": path, "port": port_num})

    if not rules:
        return None

    tls_hosts = list(dict.fromkeys(r["host"] for r in rules))
    ingress = {"rules": rules}
    if tls:
        ingress.setdefault("tls", [{
            "hosts": tls_hosts,
            "secretName": f"{tls_hosts[0].replace('.', '-')}-tls"
        }])
    ingress.setdefault("className", ingress_class)

    return ingress

