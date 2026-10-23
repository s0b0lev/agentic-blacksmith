from pathlib import Path

import pytest

from blacksmith.config import Check, ConfigError, load_config, parse_config

EXAMPLE = Path(__file__).parents[2] / "examples/sample-shop/blacksmith.yml"


def minimal() -> dict:
    return {
        "version": 1,
        "preview": {
            "compose": "docker-compose.yml",
            "project": "p",
            "routes": [{"path": "/*", "to": "web:8000"}],
        },
    }


def test_loads_sample_example():
    cfg = load_config(EXAMPLE)
    assert cfg.label == "blacksmith"
    assert cfg.preview.build["worker"] == "api"
    assert cfg.preview.routes[1].rewrite == "/media/* -> /*"
    assert cfg.checks[0].soft is True
    assert cfg.checks[1] == Check(run="cd admin && npm run typecheck")
    assert cfg.preview.auth == "basic"
    assert cfg.preview.images == {}
    assert cfg.preview.reset == {"minio": ("command", "healthcheck", "volumes")}
    assert cfg.preview.overrides["minio"]["image"] == "bitnamilegacy/minio:2025.4.22"


def test_overrides_and_reset_parsed():
    data = minimal()
    data["preview"]["overrides"] = {"web": {"image": "x:1", "environment": {"A": "b"}}}
    data["preview"]["reset"] = {"web": ["command", "healthcheck"]}
    preview = parse_config(data).preview
    assert preview.overrides == {"web": {"image": "x:1", "environment": {"A": "b"}}}
    assert preview.reset == {"web": ("command", "healthcheck")}


def test_overrides_and_reset_validated():
    data = minimal()
    data["preview"]["overrides"] = {"web": "image: x"}
    data["preview"]["reset"] = {"web": "command"}
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    lines = str(exc.value).splitlines()
    assert any(line.startswith("preview.overrides:") for line in lines)
    assert any(line.startswith("preview.reset:") for line in lines)


def test_check_as_plain_string():
    data = minimal()
    data["checks"] = ["make test"]
    assert parse_config(data).checks == (Check("make test", False),)


def test_defaults():
    cfg = parse_config(minimal())
    assert cfg.label == "blacksmith"
    assert cfg.branch_prefix == "blacksmith/issue-"
    assert cfg.droplet.size == "s-4vcpu-8gb"
    assert cfg.droplet.region == "fra1"
    assert cfg.droplet.image == "ubuntu-24-04-x64"
    assert cfg.limits.first_run_minutes == 120
    assert cfg.limits.review_round_minutes == 45
    assert cfg.limits.max_turns_per_stage == 200
    assert cfg.limits.lifetime_hours == 168
    assert cfg.setup == () and cfg.checks_setup == () and cfg.checks == ()
    assert cfg.design.figma is False and cfg.design.paths == ()
    assert cfg.preview.ready == "https://${PREVIEW_HOST}/"
    assert cfg.preview.auth == "basic"
    assert cfg.preview.build == {} and cfg.preview.images == {}
    assert cfg.preview.overrides == {} and cfg.preview.reset == {}
    assert cfg.preview.drop == () and cfg.preview.infra == ()
    assert cfg.preview.env == {}
    assert cfg.preview.env_example is None and cfg.preview.seed is None


def test_errors_are_collected():
    data = minimal()
    del data["preview"]["compose"]
    data["preview"]["auth"] = "oauth"
    data["preview"]["routes"].append({"path": "/x"})
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    lines = str(exc.value).splitlines()
    assert any(line.startswith("preview.compose:") for line in lines)
    assert any(line.startswith("preview.auth:") for line in lines)
    assert "preview.routes[1].to: missing" in lines


def test_unknown_version():
    data = minimal()
    data["version"] = 2
    with pytest.raises(ConfigError, match="version"):
        parse_config(data)


def test_unknown_keys_are_errors():
    data = minimal()
    data["lable"] = "x"
    data["preview"]["autth"] = "none"
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    lines = str(exc.value).splitlines()
    assert "lable: unknown key" in lines
    assert "preview.autth: unknown key" in lines


def test_bad_route_target():
    data = minimal()
    data["preview"]["routes"][0]["to"] = "web"
    with pytest.raises(ConfigError, match=r"preview\.routes\[0\]\.to"):
        parse_config(data)
