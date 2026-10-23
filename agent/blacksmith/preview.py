"""Preview host naming and ``.env`` rendering for per-issue droplets."""

from __future__ import annotations

import secrets
from collections.abc import Callable

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
