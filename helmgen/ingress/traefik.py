"""Traefik label and rule parsing (v1, v2 and v3 syntaxes)."""
import re

_TRAEFIK_ROUTER_KEY_RE = re.compile(r"^traefik\.(?:https?)\.routers\.[^.]+\.(.+)$", re.I)

def _traefik_field(key):
    """Traefik rule field (after the router name) from a router label key."""
    m = _TRAEFIK_ROUTER_KEY_RE.match(str(key).strip())
    return m.group(1).lower() if m else None

def _rule_hostsets(value):
    """Host sets from a Traefik rule: Host(`a`,`b`) (v3) or Host:a,b (v1/v2)."""
    sets = [
        [h.strip("`\"'") for h in re.split(r"[,;\s]+", part) if h.strip("`\"'")]
        for part in re.findall(r"Host\(([^)]*)\)", value, re.I)
    ]
    for part in re.findall(r"Host:([A-Za-z0-9.,_.*-]+)", value, re.I):
        hosts = [h.strip("`\"'") for h in part.split(",")]
        sets.append([h for h in hosts if h])
    return sets

def _rule_paths(value):
    """Paths from Path(/a) / PathPrefix(/b) (v3) or Path:/a (v1/v2) rules."""
    paths = re.findall(r"(?:PathPrefix|Path)\(([^)]*)\)", value, re.I)
    paths += re.findall(r"Path(?:Prefix)?:([^\s,;]+)", value, re.I)
    out = []
    for p in paths:
        p = p.strip().strip("`\"'")
        if p.startswith("/"):
            out.append(p)
    return out

