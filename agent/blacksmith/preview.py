"""Preview host naming and ``.env`` rendering for per-issue droplets."""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from blacksmith.config import Preview

PLACEHOLDERS = {"", "change-me", "changeme", "CHANGE_ME"}


def preview_host(issue: int, ip: str, zone: str | None) -> str:
    """Hostname serving the preview: ``pr-N.<zone>`` or an nip.io fallback."""
    if zone:
        return f"pr-{issue}.{zone}"
    return f"pr-{issue}-{ip.replace('.', '-')}.nip.io"


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def render_env(
    example: str,
    overrides: dict[str, str],
    host: str,
    rand: Callable[[], str] = secrets.token_urlsafe,
) -> str:
    """Render an example env file as ``KEY='value'`` lines.

    Placeholder values get random secrets; overrides win and may use
    ``${PREVIEW_HOST}``; override-only keys are appended.
    """
    values: dict[str, str] = {}
    for raw in example.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = _unquote(value.strip())
        values[key.strip()] = rand() if value in PLACEHOLDERS else value
    for key, value in overrides.items():
        values[key] = value.replace("${PREVIEW_HOST}", host)
    return "".join(f"{key}={_quote(value)}\n" for key, value in values.items())


@dataclass(frozen=True)
class BasicAuth:
    user: str
    password_hash: str


def compose_service_names(compose_path: Path) -> list[str]:
    """Service names from a compose file, in file order."""
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        raise ValueError(f"{compose_path}: no 'services' mapping")
    return list(services)


def render_override(
    preview: Preview, compose_services: list[str], *, caddyfile_path: str
) -> str:
    """Compose override file text (built by hand: PyYAML cannot emit ``!reset``)."""
    out = ["services:"]
    for name in compose_services:
        out.append(f"  {name}:")
        if name in preview.drop:
            out.append('    profiles: ["blacksmith-dropped"]')
            continue
        out.append("    ports: !reset []")
        if name in preview.build:
            out.append("    build:")
            out.append(f"      context: {preview.build[name]}")
            out.append("    image: !reset null")
        elif name in preview.images:
            out.append(f"    image: {preview.images[name]}")
    out += [
        "  caddy:",
        "    image: caddy:2",
        '    ports: ["80:80", "443:443"]',
        "    volumes:",
        f"      - {caddyfile_path}:/etc/caddy/Caddyfile:ro",
        "      - caddy-data:/data",
        "    restart: unless-stopped",
        "volumes:",
        "  caddy-data: {}",
    ]
    return "\n".join(out) + "\n"


_REWRITE = re.compile(r"^(?P<prefix>/\S*?)/\* -> /\*$")


def render_caddyfile(preview: Preview, host: str, auth: BasicAuth | None) -> str:
    """Caddyfile with optional basic auth and one ``handle`` block per route."""
    out = [f"{host} {{"]
    if auth:
        out += [
            "    basic_auth {",
            f"        {auth.user} {auth.password_hash}",
            "    }",
        ]
    for route in preview.routes:
        out.append(f"    handle {route.path} {{")
        if route.rewrite:
            match = _REWRITE.match(route.rewrite)
            if not match:
                raise ValueError(f"unsupported rewrite: {route.rewrite}")
            out.append(f"        uri strip_prefix {match['prefix']}")
        out.append(f"        reverse_proxy {route.to}")
        out.append("    }")
    out.append("}")
    return "\n".join(out) + "\n"
