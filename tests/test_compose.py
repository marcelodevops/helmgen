"""Tests for Compose parsing and normalization (helmgen.compose)."""

import pytest

from helmgen.compose import (
    compose_resources_to_k8s,
    normalize_depends_on,
    normalize_labels,
    normalize_networks,
    normalize_port_entry,
    parse_env_file,
    parse_port_string,
    sanitize_service_name,
)
from helmgen.flavors import detect_flavor


class TestParsePortString:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("8080:80", (8080, 80)),
            ("8080:80/tcp", (8080, 80)),
            ("127.0.0.1:8080:80", (8080, 80)),
            ("443", (None, 443)),
            ("abc", (None, None)),
        ],
    )
    def test_parsing(self, raw, expected):
        assert parse_port_string(raw) == expected

    @pytest.mark.parametrize(
        "entry, expected",
        [
            ({"target": 8080, "published": "80", "protocol": "udp"},
             {"containerPort": 8080, "published": 80, "protocol": "UDP"}),
            ({"published": 8080}, None),
            ({"target": 443, "mode": "hostip"}, {"containerPort": 443, "mode": "hostip"}),
        ],
    )
    def test_long_syntax(self, entry, expected):
        assert normalize_port_entry(entry) == expected

    def test_unparsable_string_has_no_target(self):
        assert normalize_port_entry("not-a-port") is None


class TestNormalizeLabels:
    def test_list_form(self):
        assert normalize_labels(["a=b", "c=d"]) == {"a": "b", "c": "d"}

    def test_map_form(self):
        assert normalize_labels({"a": "b"}) == {"a": "b"}

    def test_flavor_from_list_labels(self):
        assert detect_flavor({"labels": ["helmgen.flavor=payara"]}) == "payara"


class TestServiceNameSanitization:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("db", "db"),
            ("My_DB", "my-db"),
            ("my.cache", "my-cache"),
            ("1api", "1api"),  # DNS-1123 labels may start with a digit
            ("-db-", "db"),
            ("a__b", "a-b"),
            ("MyCache", "mycache"),
            ("x" * 70, "x" * 63),
        ],
    )
    def test_sanitizes(self, raw, expected):
        assert sanitize_service_name(raw) == expected

    @pytest.mark.parametrize("raw", ["", "-", "___"])
    def test_rejects_impossible_names(self, raw):
        assert sanitize_service_name(raw) is None


class TestComposeResources:
    def test_cpus_converted_to_millicores(self):
        assert compose_resources_to_k8s({"limits": {"cpus": "2.0"}}) == {
            "limits": {"cpu": "2000m"}
        }

    def test_empty(self):
        assert compose_resources_to_k8s({}) == {}


def test_parse_env_file_warns_malformed(tmp_path, capsys):
    f = tmp_path / "e.env"
    f.write_text('A=1\nB = "quoted" \nnoequals\n')
    assert parse_env_file(f) == {"A": "1", "B": "quoted"}
    assert "malformed" in capsys.readouterr().err


class TestTopologyNormalization:
    def test_depends_on_forms(self):
        assert normalize_depends_on("db") == ["db"]
        assert normalize_depends_on(["db", "cache"]) == ["db", "cache"]
        assert normalize_depends_on({"db": {"condition": "service_healthy"}}) == ["db"]
        assert normalize_depends_on(None) == []

    def test_networks_forms(self):
        assert normalize_networks(["frontend"]) == ["frontend"]
        assert normalize_networks({"frontend": {}}) == ["frontend"]
        assert normalize_networks(None) == []
