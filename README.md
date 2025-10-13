## Helmgen
Auto-generate Helm charts from Docker Compose files.

**HelmGen** is a Python CLI tool that automatically converts your `docker-compose.yml` into a fully structured Helm chart — including Deployments, Services, PVCs, Ingress, and Secrets (internal or ExternalSecrets).
##### Features:
- Convert **Docker Compose** files directly into **Helm charts**
- Auto-detect **databases** → generate StatefulSets + PVCs
- Auto-generate **Kubernetes Secrets** or **ExternalSecrets**
- Support for **Vault**, **AWS Secrets Manager**, or **ExternalSecrets Operator**
- Generate **SecretStore** or **ClusterSecretStore** automatically
- Auto-generate **Ingress** resources for web services
- Replace hardcoded secrets with safe placeholders in `values.yaml`
- Detect whether secrets should be env vars or mounted files
- CLI support for flexible options and overrides

### Installation

##### From source (recommended for development)
```bash
git clone https://github.com/yourusername/helmgen.git
cd helmgen
pip install -e . 
```
- Then you can run it as a command:
```bash
helmgen --help
```

### Usage
```bash
helmgen docker-compose.yml [options]
```
### Example
```bash
helmgen docker-compose.yml \
  --output ./charts/myapp \
  --secret-provider externalsecret \
  --store-scope cluster \
  --reuse-store global-vault-store
```


- This generates
```bash
charts/myapp/
├── Chart.yaml
├── values.yaml
└── templates/
    ├── deployment.yaml
    ├── service.yaml
    ├── pvc.yaml
    ├── ingress.yaml
    ├── secrets.yaml
    ├── externalsecret.yaml
    └── secretstore.yaml

```
- Then you can deploy
```bash
helm install myapp ./charts/myapp
```
### CLI options

| Option                  | Description                                     | Default             |
| ----------------------- | ----------------------------------------------- | ------------------- |
| `--output, -o`          | Directory for generated Helm chart              | `./generated-chart` |
| `--secret-provider, -s` | `internal` (Helm Secret) or `externalsecret`    | `internal`          |
| `--store-scope`         | `namespace` or `cluster` SecretStore            | `namespace`         |
| `--reuse-store`         | Name of existing SecretStore/ClusterSecretStore | *None*              |


##### if you want to run it as a python script (not recommended)

```bash
python3 generator.py docker-compose.yml \
  --output ./charts/myapp \
  --secret-provider externalsecret \
  --store-scope cluster \
  --reuse-store global-vault-store
  ```

##### This will create a complete Helm chart with:

```bash
charts/myapp/
├── Chart.yaml
├── values.yaml
└── templates/
    ├── deployment.yaml
    ├── service.yaml
    ├── pvc.yaml
    ├── ingress.yaml
    ├── secrets.yaml
    ├── externalsecret.yaml
    └── secretstore.yaml

```

##### Templates 

```bash
helm_templates/
├── deployment.yaml
├── service.yaml
├── pvc.yaml
├── ingress.yaml
├── secrets.yaml
├── externalsecret.yaml
└── secretstore.yaml

```

##### Summary

Files and templates directory:
- generator.py → generates chart structure and populates values.yaml.
- helm_templates/ → reusable Jinja-style templates compatible with Helm.
- Seamless handling of:

    - Secrets and ExternalSecrets
    - Databases as StatefulSets (via PVC)
    - Ingress auto-detection
    - ClusterSecretStore / SecretStore support

##### Run this version
- Generate files and templates from compose file:

```bash
python3 generator.py docker-compose.yml --output ./charts/myapp --secret-provider externalsecret
```

- Then install chart:
```bash
helm install myapp ./charts/myapp

```

##### Dependencies

| Package         | Purpose                                                                                               |
| --------------- | ----------------------------------------------------------------------------------------------------- |
| **PyYAML**      | Primary YAML parser for reading `docker-compose.yml`.                                                 |
| **ruamel.yaml** | More advanced YAML manipulation (preserves comments, ordering).                                       |
| **jinja2**      | Template rendering for Helm YAML files (used when writing `templates/`).                              |
| **click**       | Optional CLI framework (if you upgrade from `argparse` later for nicer commands).                     |
| **rich**        | Optional but recommended — adds colored console output, status spinners, and better error formatting. |


##### Install dependencies
```bash
pip install -r requirements.txt
```

##### Update to run it as a CLI tool
- Usage

```bash
helmgen docker-compose.yml --output ./charts/myapp

```

##### How it works

- project.scripts exposes a command called helmgen
- That command runs the main() function inside your generator.py
- Everything else is metadata (version, author, URLs, etc.)
- Dependencies match the ones from your requirements.txt


##### Project layout

```bash

helmgen/
├── generator.py
├── pyproject.toml
├── README.md
├── requirements.txt
└── helm_templates/
    ├── deployment.yaml
    ├── service.yaml
    ├── pvc.yaml
    ├── ingress.yaml
    ├── secrets.yaml
    ├── externalsecret.yaml
    └── secretstore.yaml
```
##### Install locally for development
- From the folder containing pyproject.toml:
```bash
pip install -e .
```
##### Then you can run it directly:
```bash
helmgen docker-compose.yml --output ./charts/myapp
```


##### Build a distributable package
- to share it or publish it to PyPI (optional):
```bash
python -m build
```
- and it generates
```bash
dist/
├── helmgen-0.1.0-py3-none-any.whl
└── helmgen-0.1.0.tar.gz

```