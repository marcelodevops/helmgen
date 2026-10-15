"""Compose healthcheck to Kubernetes probe mapping."""
import copy
import re
import shlex

def parse_compose_duration(value):
    """Convert a compose duration like '30s', '500ms' or '1m' to whole seconds."""
    m = re.match(r"^(\d+(?:\.\d+)?)(ms|s|m|h)?$", str(value).strip())
    if not m:
        return None
    num, unit = float(m.group(1)), m.group(2) or "s"
    seconds = num * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return max(int(seconds), 1)

def parse_healthcheck_probe(hc):
    """Build a base Kubernetes probe from a compose healthcheck mapping, or None."""
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

    def duration(field):
        value = hc.get(field)
        return parse_compose_duration(value) if value is not None else None

    timeout = duration("timeout")
    interval = duration("interval")
    if timeout:
        probe["timeoutSeconds"] = timeout
    if interval:
        probe["periodSeconds"] = interval
    if hc.get("retries") is not None:
        probe["failureThreshold"] = int(hc["retries"])
    return probe

def detect_probes(service):
    """Build a Kubernetes probe from a compose healthcheck, or None."""
    hc = service.get("healthcheck") or {}
    if hc.get("disable"):
        return None
    return parse_healthcheck_probe(hc)

def map_healthcheck(service):
    """Map a compose healthcheck to liveness/readiness/startup probe dicts.

    HTTP healthchecks are suitable for both liveness and readiness. Exec
    checks (pg_isready, curl-to-a-dependency) usually test reachability of
    something external, so they only become a readinessProbe — as a liveness
    probe they would restart containers whenever a dependency is down.
    compose start_period becomes a startupProbe rather than delaying liveness.
    """
    hc = service.get("healthcheck") or {}
    if hc.get("disable"):
        return {}
    probe = parse_healthcheck_probe(hc)
    if not probe:
        return {}

    probes = {}
    if "exec" in probe:
        probes["readinessProbe"] = copy.deepcopy(probe)
    else:
        probes["livenessProbe"] = copy.deepcopy(probe)
        probes["readinessProbe"] = copy.deepcopy(probe)

    start_period = parse_compose_duration(hc.get("start_period")) if hc.get("start_period") else None
    if start_period and start_period > 1:
        startup = copy.deepcopy(probe)
        startup.pop("periodSeconds", None)
        startup.pop("failureThreshold", None)
        startup["initialDelaySeconds"] = start_period
        startup.setdefault("timeoutSeconds", 3)
        startup["periodSeconds"] = 5
        startup["failureThreshold"] = 120
        probes["startupProbe"] = startup
    return probes

