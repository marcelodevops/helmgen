"""helmgen command-line interface."""
import argparse
import sys
from pathlib import Path

from .chart import generate_helm_chart

def main():
    parser = argparse.ArgumentParser(description="Generate a Helm chart from Docker Compose")
    parser.add_argument("compose_file", help="Path to docker-compose.yml")
    parser.add_argument("--output", "-o", default="./generated-chart", help="Output directory")
    parser.add_argument("--secret-provider", "-s", choices=["internal", "externalsecret"],
                        default="internal", help="Secret provider")
    parser.add_argument("--store-scope", choices=["namespace", "cluster"],
                        default="namespace", help="SecretStore scope")
    parser.add_argument("--reuse-store", default=None,
                        help="Reuse an existing SecretStore or ClusterSecretStore")
    parser.add_argument("--ingress-class", default="nginx",
                        help="ingressClassName for generated Ingress resources")
    parser.add_argument("--base-domain", default=None, metavar="DOMAIN",
                        help="Host for ingress rules when no Traefik/Host label is present "
                             "and --ingress-class is the gateway (implies TLS)")
    parser.add_argument("--values-overlay", default=None, metavar="FILE",
                        help="YAML file deep-merged into the generated values.yaml")
    parser.add_argument("--sensitive-env", default=None, metavar="NAMES",
                        help="Comma-separated env var names to treat as secrets even when "
                             "heuristic detection would not flag them (e.g. SITE_KEY)")
    parser.add_argument("--service-aliases", action="store_true",
                        help="Also expose each service under its bare compose name "
                             "(e.g. Service \"db\") so legacy host:port references resolve")
    parser.add_argument("--flavor", "-f",
                        choices=["generic", "spring-boot", "springboot", "payara"],
                        default="generic",
                        help="Template flavor preset (generic, spring-boot, payara)")
    parser.add_argument("--chart-version", default="0.1.0",
                        help="Chart version in Chart.yaml (default: 0.1.0)")
    parser.add_argument("--app-version", default="1.0.0",
                        help="Application version in Chart.yaml (default: 1.0.0)")
    parser.add_argument("--chart-name", default=None,
                        help="Chart name in Chart.yaml (defaults to output directory name)")
    args = parser.parse_args()

    compose_file = Path(args.compose_file)
    if not compose_file.exists():
        sys.exit(f"File not found: {compose_file}")

    if args.values_overlay and not Path(args.values_overlay).exists():
        sys.exit(f"Values overlay not found: {args.values_overlay}")

    generate_helm_chart(
        compose_path=compose_file,
        output_dir=Path(args.output),
        secret_provider=args.secret_provider,
        store_scope=args.store_scope,
        reuse_store=args.reuse_store,
        ingress_class=args.ingress_class,
        values_overlay=args.values_overlay,
        flavor=args.flavor,
        chart_version=args.chart_version,
        app_version=args.app_version,
        chart_name=args.chart_name,
        base_domain=args.base_domain,
        sensitive_env=args.sensitive_env,
        service_aliases=args.service_aliases,
    )

