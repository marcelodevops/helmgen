"""Tests for ingress detection (helmgen.ingress)."""
from helmgen.ingress import detect_ingress


class TestDetectIngress:
    def test_backtick_host_label(self):
        svc = {"ports": ["80"], "labels": {"rule": "Host(`web.example.com`)"}}
        ingress = detect_ingress(svc)
        assert ingress["rules"][0]["host"] == "web.example.com"

    def test_host_colon_label(self):
        svc = {"ports": ["80"], "labels": {"rule": "Host:api.example.com && PathPrefix:/api"}}
        ingress = detect_ingress(svc)
        assert ingress["rules"][0]["host"] == "api.example.com"

    def test_web_port_with_protocol(self):
        svc = {"ports": ["8080:80/tcp"], "labels": {}}
        ingress = detect_ingress(svc)
        assert ingress["rules"][0]["port"] == 80

    def test_tls_on_443(self):
        svc = {"ports": ["443:443"], "labels": {}}
        ingress = detect_ingress(svc)
        assert "tls" in ingress

    def test_no_ingress_without_web_port(self):
        svc = {"ports": ["5432:5432"], "labels": {}}
        assert detect_ingress(svc) is None

    def test_default_ingress_class(self):
        svc = {"ports": ["8080:80"], "labels": {}}
        assert detect_ingress(svc)["className"] == "nginx"

    def test_custom_ingress_class(self):
        svc = {"ports": ["8080:80"], "labels": {}}
        assert detect_ingress(svc, "traefik")["className"] == "traefik"

    def test_no_legacy_class_annotation(self):
        svc = {"ports": ["8080:80"], "labels": {}}
        assert "kubernetes.io/ingress.class" not in str(detect_ingress(svc))

    def test_multi_host_v3_rule(self):
        svc = {
            "ports": ["80"],
            "labels": {"traefik.http.routers.app.rule": "Host(`a.com`, `b.com`)"},
        }
        ingress = detect_ingress(svc)
        assert [r["host"] for r in ingress["rules"]] == ["a.com", "b.com"]
        assert ingress["rules"][0]["port"] == 80

    def test_multi_host_v1_comma(self):
        svc = {"ports": ["80"], "labels": {"rule": "Host:a.com,b.com"}}
        ingress = detect_ingress(svc)
        assert [r["host"] for r in ingress["rules"]] == ["a.com", "b.com"]

    def test_path_rule_becomes_rule_path(self):
        svc = {
            "ports": ["80"],
            "labels": {"traefik.http.routers.api.rule": "Host(`api.com`) && PathPrefix(`/v2`)"},
        }
        ingress = detect_ingress(svc)
        assert ingress["rules"][0]["path"] == "/v2"

    def test_every_path_rule_becomes_an_ingress_path(self):
        svc = {
            "ports": ["80"],
            "labels": {
                "traefik.http.routers.api.rule":
                    "Host(`api.com`) && (PathPrefix(`/v2`) || Path(`/health`))"
            },
        }
        ingress = detect_ingress(svc)
        assert ingress["rules"] == [
            {"host": "api.com", "path": "/v2", "port": 80},
            {"host": "api.com", "path": "/health", "port": 80},
        ]

    def test_tls_label_true_and_false(self):
        svc = {
            "ports": ["80"],
            "labels": {
                "traefik.http.routers.app.rule": "Host(`app.com`)",
                "traefik.http.routers.app.tls": "true",
            },
        }
        assert "tls" in detect_ingress(svc)
        svc["labels"]["traefik.http.routers.app.tls"] = "false"
        assert "tls" not in detect_ingress(svc)

    def test_tls_substring_label_does_not_enable(self):
        svc = {
            "ports": ["80"],
            "labels": {
                "traefik.enable": "true",
                "traefik.http.routers.app.rule": "Host(`app.com`)",
                "traefik.docker.network": "example-tls-backend",
            },
        }
        assert "tls" not in detect_ingress(svc)

    def test_tls_certresolver_label_enables(self):
        svc = {
            "ports": ["80"],
            "labels": {"traefik.http.routers.app.rule": "Host(`app.com`)",
                       "traefik.http.routers.app.tls.certresolver": "le"},
        }
        assert "tls" in detect_ingress(svc)

    def test_hyphenated_hostname_does_not_enable_tls(self):
        svc = {"ports": ["80"], "labels": {"rule": "Host(`foo-tls.example.com`)"}}
        ingress = detect_ingress(svc)
        assert ingress["rules"][0]["host"] == "foo-tls.example.com"
        assert "tls" not in ingress

    def test_tls_token_in_rule_enables(self):
        svc = {"ports": ["80"], "labels": {"traefik.http.routers.app.rule":
                                           "Host(`app.com`) && PathPrefix(`/`) && tls"}}
        assert "tls" in detect_ingress(svc)

    def test_https_entrypoint_router_enables_tls(self):
        svc = {"ports": ["80"], "labels": {"traefik.https.routers.app.rule": "Host(`app.com`)"}}
        assert "tls" in detect_ingress(svc)

    def test_ingress_port_is_container_not_published(self):
        svc = {"ports": ["1234:80"], "labels": {}}
        assert detect_ingress(svc)["rules"][0]["port"] == 80

    def test_base_domain_no_labels(self):
        svc = {"ports": ["8080"], "labels": {}}
        ingress = detect_ingress(svc, base_domain="app.example.com")
        assert ingress["rules"] == [{"host": "app.example.com", "path": "/", "port": 8080}]
        assert "tls" in ingress  # base domain implies HTTPS

    def test_base_domain_skips_non_web_ports(self):
        # databases and exporters must not be published through the gateway
        assert detect_ingress({"ports": ["5432"], "labels": {}},
                              base_domain="app.example.com") is None
        assert detect_ingress({"ports": [], "labels": {}},
                              base_domain="app.example.com") is None

    def test_base_domain_does_not_override_labels(self):
        svc = {"ports": ["80"], "labels": {"rule": "Host(`real.example.org`)"}}
        ingress = detect_ingress(svc, base_domain="app.example.com")
        assert ingress["rules"][0]["host"] == "real.example.org"

    def test_no_base_domain_no_ports_no_ingress(self):
        assert detect_ingress({"ports": [], "labels": {}}, base_domain=None) is None

    def test_published_only_mapping_produces_no_rule(self):
        # No container target means no Service is rendered, so no rule either.
        svc = {"ports": [{"published": 8080, "mode": "hostgw"}],
               "labels": {"rule": "Host(`app.com`)"}}
        assert detect_ingress(svc) is None

    def test_host_label_without_exposed_port_is_dropped(self):
        svc = {"ports": [], "labels": {"rule": "Host(`app.com`)"}}
        assert detect_ingress(svc) is None

    def test_long_syntax_targets_the_container_port(self):
        svc = {"labels": {"rule": "Host(`app.com`)"}}
        ports = [{"containerPort": 8080, "published": 80}]
        assert detect_ingress(svc, ports=ports)["rules"][0]["port"] == 8080

    def test_precomputed_ports_win_over_raw_compose(self):
        # generate_helm_chart passes the normalized list; ingress, Service and
        # wait-for probes must all read the same values.
        svc = {"ports": ["15432:5432"], "labels": {"rule": "Host(`app.com`)"}}
        ingress = detect_ingress(svc, ports=[{"containerPort": 5432}])
        assert ingress["rules"][0]["port"] == 5432

    def test_web_port_preferred_when_host_has_no_port_hint(self):
        svc = {"ports": ["15432:5432", "9090", "80"], "labels": {"rule": "Host(`app.com`)"}}
        assert detect_ingress(svc)["rules"][0]["port"] == 80

    def test_falls_back_to_first_port_without_web_port(self):
        svc = {"ports": ["15432:5432", "9090"], "labels": {"rule": "Host(`app.com`)"}}
        assert detect_ingress(svc)["rules"][0]["port"] == 5432
