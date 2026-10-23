from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from blacksmith.config import Route, load_config
from blacksmith.preview import (
    BasicAuth,
    compose_service_names,
    render_extra,
    render_caddyfile,
    render_override,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/sample-shop/blacksmith.yml"
JIM_COMPOSE = Path("/path/to/sample-shop/ops/docker-compose.prod.yml")
SERVICES = ["db", "minio", "minio-init", "api", "worker", "beat", "admin", "storefront"]
CADDYFILE = "/srv/app/.blacksmith/Caddyfile"
REPO = Path("/srv/app")


@pytest.fixture
def preview():
    return load_config(EXAMPLE).preview


def _override(preview):
    return render_override(preview, SERVICES, caddyfile_path=CADDYFILE, repo_root=REPO)


def test_override_resets_ports_for_all_services(preview):
    lines = _override(preview).splitlines()
    for name in SERVICES:
        if name in preview.drop:
            continue
        i = lines.index(f"  {name}:")
        assert lines[i + 1] == "    ports: !reset []", name


def test_override_build_replaces_image(preview):
    out = _override(preview)
    assert "  api:\n    ports: !reset []\n    build:\n      context: /srv/app/api\n    image: !reset null\n" in out


def test_override_images_map(preview):
    swapped = replace(preview, images={"redis": "redis:7.4"}, reset={})
    out = render_override(swapped, ["redis"], caddyfile_path=CADDYFILE, repo_root=REPO)
    assert "  redis:\n    ports: !reset []\n    image: redis:7.4\n" in out


def test_override_reset_lines(preview):
    out = _override(preview)
    assert (
        "  minio:\n    ports: !reset []\n    command: !reset null\n"
        "    healthcheck: !reset null\n    volumes: !reset null\n"
    ) in out


def test_override_reset_skips_keys_already_set(preview):
    both = replace(preview, reset={"api": ("ports", "image", "command")})
    out = _override(both)
    assert "  api:\n    ports: !reset []\n    build:\n      context: /srv/app/api\n" \
        "    image: !reset null\n    command: !reset null\n" in out


def test_extra_file_renders_overrides_verbatim(preview):
    text = render_extra(preview)
    data = yaml.safe_load(text)
    assert list(data) == ["services"]
    minio = data["services"]["minio"]
    assert minio["image"] == "bitnamilegacy/minio:2025.4.22"
    assert minio["environment"]["MINIO_DEFAULT_BUCKETS"] == "product-images:download"
    assert minio["environment"]["MINIO_ROOT_USER"] == "${MINIO_ACCESS_KEY:-sampleshop}"
    assert minio["volumes"] == ["minio-data:/bitnami/minio/data"]
    assert "!reset" not in text


def test_extra_file_none_without_overrides(preview):
    assert render_extra(replace(preview, overrides={})) is None


def test_override_build_wins_over_images(preview):
    both = replace(preview, images={**preview.images, "api": "x/y:1"})
    assert "x/y:1" not in _override(both)


def test_override_dropped_service_gets_profile(preview):
    out = _override(preview)
    assert '  minio-init:\n    profiles: ["blacksmith-dropped"]\n' in out


def test_override_has_caddy_service_publishing_only_80_443(preview):
    out = _override(preview)
    current = None
    published = []
    for line in out.splitlines():
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            current = line.strip()[:-1]
        if line.strip().startswith("ports:") and line.strip() != "ports: !reset []":
            published.append(current)
    assert published == ["caddy"]
    assert '    ports: ["80:80", "443:443"]\n' in out
    assert f"      - {CADDYFILE}:/etc/caddy/Caddyfile:ro\n" in out
    assert out.endswith("volumes:\n  caddy-data: {}\n")


def test_caddyfile_routes_in_order_with_rewrite(preview):
    out = render_caddyfile(preview, "pr-1.example.com", None)
    admin = out.index("    handle /admin* {\n        reverse_proxy admin:3000\n    }\n")
    media = out.index(
        "    handle /media/* {\n        uri strip_prefix /media\n"
        "        reverse_proxy minio:9000\n    }\n"
    )
    last = out.index("    handle /* {\n        reverse_proxy storefront:3001\n    }\n")
    assert admin < media < last
    assert out.startswith("pr-1.example.com {\n")
    assert out.endswith("}\n")


def test_caddyfile_basic_auth_block_first_when_auth(preview):
    out = render_caddyfile(preview, "h.example.com", BasicAuth("bob", "$2a$hash"))
    assert out.startswith("h.example.com {\n    basic_auth {\n        bob $2a$hash\n    }\n")
    assert out.index("basic_auth") < out.index("handle")


def test_caddyfile_no_auth_block_when_none(preview):
    assert "basic_auth" not in render_caddyfile(preview, "h.example.com", None)


def test_caddyfile_rejects_unsupported_rewrite(preview):
    bad = replace(preview, routes=(Route(path="/a/*", to="a:1", rewrite="/a/* -> /b/*"),))
    with pytest.raises(ValueError, match="/a/\\* -> /b/\\*"):
        render_caddyfile(bad, "h", None)


def test_service_names_from_jim_scale_compose():
    if not JIM_COMPOSE.exists():
        pytest.skip("sample-shop compose not present")
    assert "minio-init" in compose_service_names(JIM_COMPOSE)


def test_service_names_keep_file_order_and_require_services(tmp_path):
    f = tmp_path / "c.yml"
    f.write_text("services:\n  b: {}\n  a: {}\n")
    assert compose_service_names(f) == ["b", "a"]
    f.write_text("version: '3'\n")
    with pytest.raises(ValueError):
        compose_service_names(f)
