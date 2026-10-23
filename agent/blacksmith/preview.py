"""Preview host naming and ``.env`` rendering for per-issue droplets."""

from __future__ import annotations

import base64
import json
import re
import secrets
import shlex
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from blacksmith.config import Config, Preview
from blacksmith.runner import CommandResult, CommandRunner

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
    """Quote a dotenv value for compose: literal single quotes unless it holds ``'``.

    Compose's dotenv parser has no escape inside single quotes, so a value with
    ``'`` is double-quoted with backslash, double quote and dollar each escaped
    by a backslash (double quotes would otherwise interpolate ``${VAR}``).
    """
    if "'" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def _env_values(
    example: str,
    overrides: Mapping[str, str],
    host: str,
    rand: Callable[[], str],
    existing: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """``(all values, generated placeholder values)`` for an example env file."""
    values: dict[str, str] = {}
    generated: dict[str, str] = {}
    for raw in example.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), _unquote(value.strip())
        if value in PLACEHOLDERS:
            value = existing[key] if key in existing else rand()
            generated[key] = value
        values[key] = value
    for key, value in overrides.items():
        values[key] = value.replace("${PREVIEW_HOST}", host)
    return values, generated


def _format_env(values: Mapping[str, str]) -> str:
    return "".join(f"{key}={_quote(value)}\n" for key, value in values.items())


def render_env(
    example: str,
    overrides: Mapping[str, str],
    host: str,
    rand: Callable[[], str] = secrets.token_urlsafe,
    existing: Mapping[str, str] | None = None,
) -> str:
    """Render an example env file as quoted ``KEY=value`` lines.

    Placeholder values take the value in ``existing`` for that key, else a
    random secret; overrides win and may use ``${PREVIEW_HOST}``; override-only
    keys are appended.
    """
    values, _ = _env_values(example, overrides, host, rand, existing or {})
    return _format_env(values)


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
    preview: Preview, compose_services: list[str], *, caddyfile_path: str, repo_root: Path
) -> str:
    """Compose override file text (built by hand: PyYAML cannot emit ``!reset``).

    Build contexts are absolute so the file does not depend on the compose
    project directory.
    """
    out = ["services:"]
    for name in compose_services:
        out.append(f"  {name}:")
        if name in preview.drop:
            out.append('    profiles: ["blacksmith-dropped"]')
            continue
        out.append("    ports: !reset []")
        done = {"ports"}
        if name in preview.build:
            out.append("    build:")
            out.append(f"      context: {repo_root / preview.build[name]}")
            out.append("    image: !reset null")
            done.add("image")
        elif name in preview.images:
            out.append(f"    image: {preview.images[name]}")
            done.add("image")
        for key in preview.reset.get(name, ()):
            if key not in done:
                out.append(f"    {key}: !reset null")
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


def render_extra(preview: Preview) -> str | None:
    """``preview.overrides`` as a compose file (applied after the override), or None."""
    if not preview.overrides:
        return None
    return yaml.safe_dump({"services": preview.overrides}, sort_keys=False)


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


@dataclass(frozen=True)
class PreviewContext:
    repo_root: Path
    host: str
    work_dir: Path


@dataclass(frozen=True)
class PreviewResult:
    ok: bool
    url: str
    auth: tuple[str, str] | None
    error: str | None
    diagnostics: str


def _urllib_status(url: str, auth: tuple[str, str] | None) -> int:
    """HTTP status of a GET; 0 when the server cannot be reached."""
    request = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as err:
        return err.code
    except (urllib.error.URLError, OSError):
        return 0


def _depends_on_dropped(compose_path: Path, drop: tuple[str, ...]) -> str | None:
    """Error text if a kept service depends_on a dropped one, else None."""
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    for name, service in data["services"].items():
        if name in drop or not isinstance(service, dict):
            continue
        for dep in service.get("depends_on") or []:  # list or mapping: keys
            if dep in drop:
                return f"service '{name}' depends_on dropped service '{dep}'"
    return None


SECRETS_FILE = "preview-secrets.json"


def _load_secrets(work_dir: Path) -> dict:
    """Values generated by an earlier deploy on this droplet, or ``{}``."""
    try:
        data = json.loads((work_dir / SECRETS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_secrets(work_dir: Path, data: dict) -> None:
    path = work_dir / SECRETS_FILE
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    path.chmod(0o600)


def _write_inputs(
    preview: Preview,
    ctx: PreviewContext,
    compose_path: Path,
    auth: tuple[str, str] | None,
    password_hash: str | None,
    rand: Callable[[], str],
    existing_env: Mapping[str, str],
) -> tuple[Path, dict[str, str], bool]:
    """Write ``.env`` (next to the compose file), the override and the Caddyfile.

    Returns the env path, the generated placeholder values, and whether an
    existing Caddyfile changed (so Caddy must be recreated to pick it up).
    """
    env_path = compose_path.parent / ".env"
    example = ""
    if preview.env_example:
        example = (ctx.repo_root / preview.env_example).read_text(encoding="utf-8")
    values, generated = _env_values(example, preview.env, ctx.host, rand, existing_env)
    env_path.write_text(_format_env(values))
    caddyfile = ctx.work_dir / "Caddyfile"
    basic = BasicAuth(auth[0], password_hash) if auth and password_hash else None
    text = render_caddyfile(preview, ctx.host, basic)
    previous = caddyfile.read_text() if caddyfile.exists() else None
    caddyfile.write_text(text)
    override = render_override(
        preview, compose_service_names(compose_path), caddyfile_path=str(caddyfile),
        repo_root=ctx.repo_root,
    )
    (ctx.work_dir / "compose.override.yml").write_text(override)
    extra = ctx.work_dir / "compose.extra.yml"
    extra_text = render_extra(preview)
    if extra_text is not None:
        extra.write_text(extra_text)
    elif extra.exists():
        extra.unlink()
    return env_path, generated, previous is not None and previous != text


def _fail(
    run: CommandRunner,
    base: list[str],
    cwd: Path,
    step: str,
    output: str,
    error: str,
    auth: tuple[str, str] | None,
    url: str,
) -> PreviewResult:
    parts = [f"== {step} ==\n{output}"]
    for label, extra in (("ps", ["ps"]), ("logs", ["logs", "--tail", "100"])):
        res = run(base + extra, cwd=cwd, timeout=60)
        parts.append(f"== {label} ==\n{res.stdout}{res.stderr}")
    return PreviewResult(False, url, auth, error, "\n".join(parts))


def _wait_ready(
    url: str,
    auth: tuple[str, str] | None,
    timeout_s: int,
    http_get: Callable[[str, tuple[str, str] | None], int],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> int | None:
    """Poll every 5 s; None once ready, else the last status seen."""
    deadline = clock() + timeout_s
    while True:
        try:
            status = http_get(url, auth)
        except Exception:
            status = 0
        if 200 <= status < 400:
            return None
        if clock() >= deadline:
            return status
        sleep(5)


def _out(res: CommandResult) -> str:
    return res.stdout + res.stderr


def deploy(
    cfg: Config,
    ctx: PreviewContext,
    run: CommandRunner,
    *,
    ready_timeout_s: int = 600,
    http_get: Callable[[str, tuple[str, str] | None], int] = _urllib_status,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    rand: Callable[[], str] = secrets.token_urlsafe,
) -> PreviewResult:
    """Bring the preview stack up and wait until it answers."""
    preview = cfg.preview
    url = preview.ready.replace("${PREVIEW_HOST}", ctx.host)
    compose_path = ctx.repo_root / preview.compose
    conflict = _depends_on_dropped(compose_path, preview.drop)
    if conflict:
        return PreviewResult(False, url, None, conflict, "")

    stored = _load_secrets(ctx.work_dir)
    auth: tuple[str, str] | None = None
    password_hash = None
    if preview.auth == "basic":
        saved = stored.get("auth")
        if isinstance(saved, list) and len(saved) == 3 and all(isinstance(v, str) for v in saved):
            auth, password_hash = (saved[0], saved[1]), saved[2]
        else:
            auth = ("preview", rand())
            cmd = ["docker", "run", "--rm", "caddy:2", "caddy", "hash-password",
                   "--plaintext", auth[1]]  # fmt: skip
            res = run(cmd, cwd=ctx.repo_root, timeout=300)
            if res.code != 0:
                diag = f"== hash-password ==\n{_out(res)}"
                return PreviewResult(False, url, None, "hash-password failed", diag)
            password_hash = res.stdout.strip()
    existing_env = stored.get("env_values")
    if not isinstance(existing_env, dict):
        existing_env = {}
    env_path, generated, caddy_changed = _write_inputs(
        preview, ctx, compose_path, auth, password_hash, rand, existing_env
    )
    _save_secrets(ctx.work_dir, {
        "env_values": generated,
        "auth": [auth[0], auth[1], password_hash] if auth and password_hash else None,
    })

    # No --project-directory: compose resolves .env, env_file and relative paths
    # against the first file's directory, the same for `up` and for the seed.
    override = ctx.work_dir / "compose.override.yml"
    files = [compose_path, override]
    if preview.overrides:  # last, so the adopter's fragments win
        files.append(ctx.work_dir / "compose.extra.yml")
    base = ["docker", "compose", "-p", preview.project]
    for f in files:
        base += ["-f", str(f)]
    up = ["up", "-d", "--build", "--wait"]
    steps: list[tuple[str, list[str], int]] = []
    if preview.infra:
        steps.append(("up infra", base + up + list(preview.infra), 1800))
    if preview.seed:
        q = shlex.quote
        compose_file = ":".join(str(f) for f in files)
        env = (f"export COMPOSE_PROJECT_NAME={q(preview.project)} "
               f"COMPOSE_FILE={q(compose_file)} COMPOSE_PATH_SEPARATOR=: "
               f"&& cd {q(str(compose_path.parent))} && {preview.seed}")  # fmt: skip
        steps.append(("seed", ["bash", "-lc", env], 900))
    steps.append(("up", base + up, 1800))
    if caddy_changed:
        recreate = ["up", "-d", "--no-deps", "--force-recreate", "caddy"]
        steps.append(("recreate caddy", base + recreate, 300))
    for step, cmd, timeout in steps:
        res = run(cmd, cwd=ctx.repo_root, timeout=timeout)
        if res.code != 0:
            error = f"{step} failed (exit {res.code})"
            return _fail(run, base, ctx.repo_root, step, _out(res), error, auth, url)

    last = _wait_ready(url, auth, ready_timeout_s, http_get, sleep, clock)
    if last is not None:
        error = f"ready probe timed out after {ready_timeout_s}s (last status {last})"
        return _fail(run, base, ctx.repo_root, "ready", error, error, auth, url)

    meta = {"url": url, "host": ctx.host, "auth": list(auth) if auth else None,
            "env_path": str(env_path)}  # fmt: skip
    (ctx.work_dir / "preview.json").write_text(json.dumps(meta, indent=2))
    return PreviewResult(True, url, auth, None, "")
