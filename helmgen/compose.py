"""Docker Compose parsing and normalization.

Understands the Compose schema only, never Kubernetes resources, so
``helmgen.chart`` can rely on this as the one place where compose quirks
(short vs long syntax, list vs map labels, env files) get flattened.
"""
import re
import sys
from pathlib import Path

def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


def normalize_labels(labels):
    """Compose labels may be a map or a KEY=VAL list; return a flat dict."""
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items()}
    out = {}
    for item in labels or []:
        if isinstance(item, str) and "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
        elif isinstance(item, dict):
            out.update(normalize_labels(item))
    return out


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

def normalize_port_entry(p):
    """Normalize a compose port (short string or long-syntax map) to
    {containerPort, published?, protocol?, mode?}. Host-only mappings without
    a container target (e.g. "127.0.0.1:8080:8080" style host_ip+published with
    no target, or long syntax with only published) return None."""
    if isinstance(p, str):
        published, container = parse_port_string(p)
        if container is None:
            return None
        entry = {"containerPort": container}
        if published is not None:
            entry["published"] = published
        if "/udp" in p.lower():
            entry["protocol"] = "UDP"
        return entry
    if isinstance(p, dict):
        target = p.get("target") or p.get("containerPort")
        if target is None:
            return None
        entry = {"containerPort": int(target)}
        if p.get("published") is not None:
            entry["published"] = int(p["published"])
        if p.get("protocol"):
            entry["protocol"] = str(p["protocol"]).upper()
        if p.get("mode"):
            entry["mode"] = str(p["mode"])
        return entry
    return None

def normalize_ports(ports):
    """Normalize compose ports, skipping host-only entries with a warning."""
    out = []
    for p in ports or []:
        entry = normalize_port_entry(p)
        if entry is None:
            warn(f"ignoring port mapping {p!r}: no container target port")
            continue
        out.append(entry)
    return out

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


def normalize_depends_on(depends_on):
    """Compose depends_on accepts a string, list, or long-syntax map; return a list of names."""
    if not depends_on:
        return []
    if isinstance(depends_on, str):
        return [depends_on]
    if isinstance(depends_on, dict):
        return list(depends_on.keys())
    return [str(d) for d in depends_on]

def normalize_networks(networks):
    """Compose networks may be a list of names or a map; return a list of names."""
    if not networks:
        return []
    if isinstance(networks, dict):
        return list(networks.keys())
    return [str(n) for n in networks]


DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

def sanitize_service_name(name):
    """Normalize a compose service name to a DNS-1123 label, or None if impossible."""
    label = re.sub(r"[^a-z0-9-]+", "-", str(name).lower())
    label = re.sub(r"-{2,}", "-", label)[:63].strip("-")
    return label if DNS_LABEL_RE.match(label) else None


def normalize_volume_mounts(volumes, storage_size="1Gi"):
    """Convert named Compose volumes to per-pod PVC-backed mounts."""
    mounts = []
    source_names = {}
    used_names = set()
    for index, volume in enumerate(volumes or []):
        source = ""
        read_only = False
        if isinstance(volume, str):
            parts = volume.split(":")
            if len(parts) == 1:
                target = parts[0]
            else:
                source, target = parts[0], parts[1]
                read_only = len(parts) > 2 and "ro" in parts[2].split(",")
            mount_type = (
                "bind"
                if source.startswith(("/", "./", "../", "~/", ".\\", "..\\"))
                or "/" in source
                or re.match(r"^[A-Za-z]:[\\/]", source)
                else "volume"
            )
        elif isinstance(volume, dict):
            source = str(volume.get("source") or "")
            target = volume.get("target")
            read_only = bool(volume.get("read_only", False))
            mount_type = volume.get("type") or (
                "bind" if source.startswith(("/", "./", "../", "~/")) else "volume"
            )
        else:
            warn(f"ignoring unsupported volume entry {volume!r}")
            continue

        if not target:
            warn(f"ignoring volume entry {volume!r}: no container target")
            continue
        if mount_type != "volume":
            warn(
                f"service volume {volume!r} is a {mount_type} mount and cannot be "
                "represented portably in a Helm chart; configure it manually"
            )
            continue

        identity = source or f"anonymous-{index + 1}"
        if identity not in source_names:
            slug = re.sub(r"[^a-z0-9-]+", "-", identity.lower()).strip("-") or "data"
            base = f"volume-{slug[:55]}".rstrip("-")
            name = base
            suffix = 2
            while name in used_names:
                ending = f"-{suffix}"
                name = f"{base[:63 - len(ending)]}{ending}"
                suffix += 1
            source_names[identity] = name
            used_names.add(name)

        mounts.append({
            "name": source_names[identity],
            "mountPath": str(target),
            "size": storage_size,
            "readOnly": read_only,
        })
    return mounts


def parse_env_file(path):
    """Parse a docker --env-file style KEY=VALUE file. No shell interpolation."""
    env = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            warn(f"ignoring malformed line in env_file {path}: {raw!r}")
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        env[k] = v
    return env


def load_env_files(env_file, base_dir):
    """Merge compose env_file entries (legacy strings or {path, required} maps).

    Later files win over earlier ones, matching the compose spec; the
    service's inline `environment` wins over all of them.
    """
    if isinstance(env_file, (str, dict)):
        env_file = [env_file]
    merged = {}
    for entry in env_file or []:
        if isinstance(entry, dict):
            name = entry.get("path")
            required = entry.get("required", True)
        else:
            name = str(entry)
            required = True
        if not name:
            continue
        p = Path(name)
        if not p.is_absolute():
            p = base_dir / p
        if not p.exists():
            if required:
                warn(f"env_file not found: {p}")
            continue
        merged.update(parse_env_file(p))
    return merged

