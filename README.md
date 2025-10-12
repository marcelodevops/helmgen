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