# Helmgen

Auto-generate Helm charts from Docker Compose files.

**HelmGen** is a Python CLI tool that converts your `docker-compose.yml` into a structured Helm chart — including Deployments, StatefulSets, Services, PVCs, Ingress, and Secrets (native or ExternalSecrets).

## Features

- Convert **Docker Compose** files directly into **Helm charts**
- Auto-detect **databases** (postgres, mysql, mariadb, mongodb, redis) → StatefulSet + PVC with real volume mount paths
- Auto-generate **Kubernetes Secrets** or **ExternalSecrets** (External Secrets Operator)
- Generate **SecretStore** or **ClusterSecretStore**, or reuse an existing one
- Auto-detect **Ingress** from exposed web ports (80/443/8080) and Traefik-style `Host(...)` labels
- Replace hardcoded secrets with safe placeholders in `values.yaml`
- Sensible storage size defaults per database type
- **Workload flavors** (`--flavor`): presets for **Spring Boot** (Actuator probes, startup probe, JVM memory) and **Payara** (MicroProfile `/health/*`, startup probe, admin port protection) with auto-detection
- Maps compose `replicas`, `command`/`args`, `deploy.resources`, `healthcheck` (→ liveness/readiness probes), and `depends_on`/`networks` into the chart
- Optional wait-for initContainers (`waitOnDependencies: true`) to gate startup on dependency Services
- Default CPU/memory `resources` on all containers, overridable per service in `values.yaml`
- Optional HTTP liveness/readiness probes via `probePath`/`probePort` in `values.yaml`
- Standard `app.kubernetes.io/*` labels on every resource (via `_helpers.tpl`)

## Installation

```bash
pip install helmgen
```

From source (recommended for development):

```bash
git clone https://github.com/marcelodevops/helmgen.git
cd helmgen
pip install -e .
```

## Usage

```bash
helmgen docker-compose.yml [options]
```

| Option                  | Description                                     | Default             |
| ----------------------- | ----------------------------------------------- | ------------------- |
| `--output, -o`          | Directory for generated Helm chart              | `./generated-chart` |
| `--secret-provider, -s` | `internal` (Helm Secret) or `externalsecret`    | `internal`          |
| `--store-scope`         | `namespace` or `cluster` SecretStore            | `namespace`         |
| `--reuse-store`         | Name of existing SecretStore/ClusterSecretStore | *None*              |
| `--ingress-class`       | `ingressClassName` for generated Ingress        | `nginx`             |
| `--values-overlay`      | YAML file deep-merged into generated values     | *None*              |
| `--flavor, -f`          | Template flavor (`generic`, `spring-boot`, `payara`) | `generic`      |
| `--chart-version`       | Chart version in `Chart.yaml`                   | `0.1.0`             |
| `--app-version`         | Application version in `Chart.yaml`             | `1.0.0`             |
| `--chart-name`          | Chart name in `Chart.yaml`                      | *Directory name*    |

### Workload Flavors (Spring Boot & Payara)

JVM runtimes require distinct lifecycle handling compared to lightweight Go or Node services: long initialization times that trigger false-positive liveness failures, framework-specific health check paths, higher baseline heap memory, and multi-port container layouts (e.g. Payara admin port 4848).

HelmGen provides built-in flavor presets:

- **Spring Boot** (`spring-boot` or alias `springboot` / `spring`):
  - **Liveness probe**: HTTP GET `/actuator/health/liveness` (port 8080 or first container port)
  - **Readiness probe**: HTTP GET `/actuator/health/readiness` (port 8080 or first container port)
  - **Startup probe**: HTTP GET `/actuator/health/liveness` (`failureThreshold: 20`, `periodSeconds: 5` = up to 100s boot window before liveness triggers)
  - **Resource defaults**: Requests `100m` CPU / `512Mi` memory; Limits `1` CPU / `1Gi` memory
- **Payara** (`payara`):
  - **Liveness probe**: HTTP GET `/health/live` (port 8080, automatically bypassing admin port 4848)
  - **Readiness probe**: HTTP GET `/health/ready` (port 8080)
  - **Startup probe**: HTTP GET `/health/live` (`failureThreshold: 30`, `periodSeconds: 5` = up to 150s startup/deployment window)
  - **Resource defaults**: Requests `200m` CPU / `512Mi` memory; Limits `2` CPU / `2Gi` memory

#### Flavor Resolution Hierarchy

HelmGen determines each service's flavor using the following precedence:

1. **Service Label** in `docker-compose.yml`: `helmgen.flavor: "spring-boot"` or `flavor: "payara"`
2. **Image Name Heuristic**: Images containing `payara` resolve to `payara`; images containing `spring-boot` or `springboot` resolve to `spring-boot`
3. **CLI Default**: `--flavor <name>` (defaults to `generic` when omitted)

#### Example: Mixed Workload in `docker-compose.yml`

```yaml
version: "3.8"
services:
  # Explicitly labeled flavor
  order-service:
    image: myrepo/orders:1.0
    labels:
      helmgen.flavor: "spring-boot"
    ports:
      - "8080:8080"

  # Auto-detected via image name
  legacy-app:
    image: payara/micro:6.2023.4
    ports:
      - "8080:8080"
      - "4848:4848"

  # Falls back to generic flavor (or --flavor default)
  cache:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

#### Generated `values.yaml` Snippet

When a flavor is applied, `values.yaml` includes the tailored probes and resource defaults:

```yaml
services:
  order-service:
    flavor: spring-boot
    image: myrepo/orders:1.0
    ports:
      - containerPort: 8080
    startupProbe:
      httpGet:
        path: /actuator/health/liveness
        port: 8080
      initialDelaySeconds: 10
      periodSeconds: 5
      timeoutSeconds: 3
      failureThreshold: 20
    livenessProbe:
      httpGet:
        path: /actuator/health/liveness
        port: 8080
      initialDelaySeconds: 20
      periodSeconds: 15
      timeoutSeconds: 3
      failureThreshold: 3
    readinessProbe:
      httpGet:
        path: /actuator/health/readiness
        port: 8080
      initialDelaySeconds: 10
      periodSeconds: 10
      timeoutSeconds: 3
      failureThreshold: 3
    resources:
      requests:
        cpu: 100m
        memory: 512Mi
      limits:
        cpu: "1"
        memory: 1Gi
```

> **Note**: Explicit compose configurations always take precedence. If a service defines its own `healthcheck` in compose, that check is mapped to `livenessProbe` and `readinessProbe` while preserving the generated `startupProbe`. Likewise, any `deploy.resources` defined in compose overrides flavor resource defaults.

### Example

`docker-compose.yml`:

```yaml
version: "3.8"
services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"
    environment:
      APP_ENV: production
      SECRET_KEY: supersecret

  db:
    image: postgres:14
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass123
    volumes:
      - db-data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

volumes:
  db-data:
```

> Hardcoded secrets in compose files are bad practice — HelmGen detects them and replaces their values in `values.yaml` with `<secret-from-values>` placeholders.

Generate a chart using ExternalSecrets backed by a shared ClusterSecretStore:

```bash
helmgen docker-compose.yml \
  --output ./charts/myapp \
  --secret-provider externalsecret \
  --store-scope cluster \
  --reuse-store global-vault-store
```

Output:

```
charts/myapp/
├── Chart.yaml
├── values.yaml
└── templates/
    ├── _helpers.tpl          # standard label helpers
    ├── deployment.yaml       # non-database services
    ├── statefulset.yaml      # database services (storage: true)
    ├── service.yaml
    ├── pvc.yaml
    ├── ingress.yaml
    ├── secrets.yaml          # rendered only when secretProvider=internal
    ├── externalsecret.yaml   # rendered only when secretProvider=externalsecret
    └── secretstore.yaml      # skipped when --reuse-store is set
```

A ready-to-try input lives in [`examples/docker-compose.yml`](examples/docker-compose.yml), and a Spring Boot / Payara flavor example is in [`examples/docker-compose-flavors.yml`](examples/docker-compose-flavors.yml).

### Tuning the generated chart

`values.yaml` is generated with a documented header describing every tunable;
edit it directly, or pass `--values-overlay` so customizations survive regeneration:

```yaml
# my-overrides.yml
waitOnDependencies: true
services:
  web:
    replicas: 3
```

```yaml
services:
  web:
    probePath: /healthz   # adds liveness + readiness httpGet probes
    probePort: 8080
    replicas: 3
    resources:            # overrides the global default per service
      requests: {cpu: 50m, memory: 256Mi}
resources:                # global default for all containers
  requests: {cpu: 10m, memory: 128Mi}
  limits: {cpu: "1", memory: 512Mi}
```

Deploy:

```bash
helm install myapp ./charts/myapp
```

## Development

```bash
pip install -e .[dev]
ruff check .
pytest --cov=helmgen
```

The templates in `helmgen/helm_templates/` are plain Helm templates (copied verbatim into the generated chart); all chart logic driven by `values.yaml` lives in `helmgen/generator.py`.

## Dependencies

| Package    | Purpose                              |
| ---------- | ------------------------------------ |
| **PyYAML** | Parse `docker-compose.yml` and emit `values.yaml` |

## License

MIT
