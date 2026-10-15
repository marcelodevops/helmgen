"""Template flavor presets (Spring Boot, Payara) applied to service values."""
from .compose import normalize_labels
from .presets import FLAVOR_ALIASES, FLAVOR_RESOURCES, SUPPORTED_FLAVORS

def detect_flavor(svc, default_flavor="generic"):
    """
    Detect the service flavor ('generic', 'spring-boot', 'payara').
    Checks service labels ('helmgen.flavor', 'flavor'), image name heuristics,
    or falls back to default_flavor.
    """
    labels = normalize_labels(svc.get("labels"))
    for key in ("helmgen.flavor", "flavor"):
        val = labels.get(key)
        if val:
            normalized = FLAVOR_ALIASES.get(str(val).lower(), str(val).lower())
            if normalized in SUPPORTED_FLAVORS:
                return normalized

    image = str(svc.get("image", "")).lower()
    if "payara" in image:
        return "payara"
    if "spring-boot" in image or "springboot" in image:
        return "spring-boot"

    return FLAVOR_ALIASES.get(str(default_flavor).lower(), str(default_flavor).lower())


def apply_flavor(service_data, flavor):
    """Apply flavor-specific probe and resource defaults to service_data."""
    if flavor not in ("spring-boot", "payara"):
        return

    service_data["flavor"] = flavor

    # Determine probe container port
    ports = service_data.get("ports", [])
    container_ports = [p["containerPort"] for p in ports if "containerPort" in p]
    if flavor == "payara":
        # 8080 is the default HTTP application port; 4848 is admin console
        if 8080 in container_ports:
            probe_port = 8080
        else:
            non_admin = [cp for cp in container_ports if cp != 4848]
            probe_port = non_admin[0] if non_admin else 8080
    else:
        # spring-boot
        probe_port = (
            8080 if (8080 in container_ports or not container_ports) else container_ports[0]
        )

    # Flavor-specific probes (only if not already provided via compose healthcheck)
    if flavor == "spring-boot":
        if "livenessProbe" not in service_data:
            service_data["livenessProbe"] = {
                "httpGet": {"path": "/actuator/health/liveness", "port": probe_port},
                "initialDelaySeconds": 20,
                "periodSeconds": 15,
                "timeoutSeconds": 3,
                "failureThreshold": 3,
            }
        if "readinessProbe" not in service_data:
            service_data["readinessProbe"] = {
                "httpGet": {"path": "/actuator/health/readiness", "port": probe_port},
                "initialDelaySeconds": 10,
                "periodSeconds": 10,
                "timeoutSeconds": 3,
                "failureThreshold": 3,
            }
        if "startupProbe" not in service_data:
            service_data["startupProbe"] = {
                "httpGet": {"path": "/actuator/health/liveness", "port": probe_port},
                "initialDelaySeconds": 10,
                "periodSeconds": 5,
                "timeoutSeconds": 3,
                "failureThreshold": 20,
            }
    elif flavor == "payara":
        if "livenessProbe" not in service_data:
            service_data["livenessProbe"] = {
                "httpGet": {"path": "/health/live", "port": probe_port},
                "initialDelaySeconds": 30,
                "periodSeconds": 15,
                "timeoutSeconds": 5,
                "failureThreshold": 3,
            }
        if "readinessProbe" not in service_data:
            service_data["readinessProbe"] = {
                "httpGet": {"path": "/health/ready", "port": probe_port},
                "initialDelaySeconds": 20,
                "periodSeconds": 10,
                "timeoutSeconds": 5,
                "failureThreshold": 3,
            }
        if "startupProbe" not in service_data:
            service_data["startupProbe"] = {
                "httpGet": {"path": "/health/live", "port": probe_port},
                "initialDelaySeconds": 15,
                "periodSeconds": 5,
                "timeoutSeconds": 5,
                "failureThreshold": 30,
            }

    # Flavor-specific default resources if not specified in compose deploy.resources
    if "resources" not in service_data and flavor in FLAVOR_RESOURCES:
        service_data["resources"] = FLAVOR_RESOURCES[flavor]

