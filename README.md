# helmgen
Auto-generate Helm charts from Docker Compose files

### This project:
- Reads a Docker Compose YAML file
- Auto-detects databases, secrets, volumes, ingress, etc.
- Generates Helm chart structure
- Supports internal or external secrets
- Supports SecretStore reuse and cluster/namespace scope
- Writes all templates (deployment, service, pvc, ingress, secrets, externalsecret, secretstore)
### if you want to run it as a python script

```bash
python3 generator.py docker-compose.yml \
  --output ./charts/myapp \
  --secret-provider externalsecret \
  --store-scope cluster \
  --reuse-store global-vault-store
  ```

### This will create a complete Helm chart with:

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

### Templates 

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

### Summary

Files and templates directory:
- generator.py → generates chart structure and populates values.yaml.
- helm_templates/ → reusable Jinja-style templates compatible with Helm.
- Seamless handling of:

    - Secrets and ExternalSecrets
    - Databases as StatefulSets (via PVC)
    - Ingress auto-detection
    - ClusterSecretStore / SecretStore support

### Run this version
- Generate files and templates from compose file:

```bash
python3 generator.py docker-compose.yml --output ./charts/myapp --secret-provider externalsecret
```

- Then install chart:
```bash
helm install myapp ./charts/myapp

```

### Dependencies

| Package         | Purpose                                                                                               |
| --------------- | ----------------------------------------------------------------------------------------------------- |
| **PyYAML**      | Primary YAML parser for reading `docker-compose.yml`.                                                 |
| **ruamel.yaml** | More advanced YAML manipulation (preserves comments, ordering).                                       |
| **jinja2**      | Template rendering for Helm YAML files (used when writing `templates/`).                              |
| **click**       | Optional CLI framework (if you upgrade from `argparse` later for nicer commands).                     |
| **rich**        | Optional but recommended — adds colored console output, status spinners, and better error formatting. |


### Install dependencies
```bash
pip install -r requirements.txt
```

### Update to run it as a CLI tool
- Usage

```bash
helmgen docker-compose.yml --output ./charts/myapp

```

### How it works

- project.scripts exposes a command called helmgen
- That command runs the main() function inside your generator.py
- Everything else is metadata (version, author, URLs, etc.)
- Dependencies match the ones from your requirements.txt


### Project layout

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
### Install locally for development
- From the folder containing pyproject.toml:
```bash
pip install -e .
```
### Then you can run it directly:
```bash
helmgen docker-compose.yml --output ./charts/myapp
```


### Build a distributable package