"""Loader and validator for the adopting project's ``blacksmith.yml``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_TARGET = re.compile(r"^[a-z0-9_-]+:\d+$")
_AUTH = ("basic", "none")


class ConfigError(ValueError):
    """Raised with every problem found, one per line, prefixed by dotted key."""


@dataclass(frozen=True)
class Check:
    run: str
    soft: bool = False


@dataclass(frozen=True)
class Route:
    path: str
    to: str
    rewrite: str | None = None


@dataclass(frozen=True)
class Droplet:
    size: str = "s-4vcpu-8gb"
    region: str = "fra1"
    image: str = "ubuntu-24-04-x64"


@dataclass(frozen=True)
class Limits:
    first_run_minutes: int = 120
    review_round_minutes: int = 45
    max_turns_per_stage: int = 200
    lifetime_hours: int = 168


@dataclass(frozen=True)
class Design:
    figma: bool = False
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Preview:
    compose: str
    project: str
    build: dict[str, str]
    images: dict[str, str]
    overrides: dict[str, dict]
    reset: dict[str, tuple[str, ...]]
    drop: tuple[str, ...]
    env_example: str | None
    env: dict[str, str]
    infra: tuple[str, ...]
    seed: str | None
    routes: tuple[Route, ...]
    ready: str
    auth: str


@dataclass(frozen=True)
class Config:
    version: int
    label: str
    branch_prefix: str
    droplet: Droplet
    limits: Limits
    setup: tuple[str, ...]
    checks_setup: tuple[str, ...]
    checks: tuple[Check, ...]
    preview: Preview
    design: Design
    allowed_labelers: tuple[str, ...] = ()


class _Ctx:
    """Accumulates problems while walking the raw data."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def err(self, key: str, msg: str) -> None:
        self.errors.append(f"{key}: {msg}")

    def section(self, data: Any, key: str, allowed: set[str], required: bool = False) -> dict:
        """Return a mapping for ``key`` with unknown keys reported."""
        if data is None:
            if required:
                self.err(key, "missing")
            return {}
        if not isinstance(data, dict):
            self.err(key, "must be a mapping")
            return {}
        for k in data:
            if k not in allowed:
                self.err(f"{key}.{k}" if key else str(k), "unknown key")
        return data

    def string(self, d: dict, key: str, path: str, default: str | None = None,
               required: bool = False) -> str | None:
        full = f"{path}.{key}" if path else key
        v = d.get(key)
        if v is None:
            if required:
                self.err(full, "missing")
            return default
        if not isinstance(v, str) or not v:
            self.err(full, "must be a non-empty string")
            return default
        return v

    def integer(self, d: dict, key: str, path: str, default: int) -> int:
        full = f"{path}.{key}"
        v = d.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            self.err(full, "must be a positive integer")
            return default
        return v

    def strings(self, d: dict, key: str, path: str) -> tuple[str, ...]:
        full = f"{path}.{key}" if path else key
        v = d.get(key)
        if v is None:
            return ()
        if not isinstance(v, list) or not all(isinstance(i, str) and i for i in v):
            self.err(full, "must be a list of non-empty strings")
            return ()
        return tuple(v)

    def str_map(self, d: dict, key: str, path: str) -> dict[str, str]:
        full = f"{path}.{key}"
        v = d.get(key)
        if v is None:
            return {}
        if not isinstance(v, dict) or not all(
            isinstance(k, str) and isinstance(i, str) for k, i in v.items()
        ):
            self.err(full, "must be a mapping of strings")
            return {}
        return dict(v)

    def boolean(self, d: dict, key: str, path: str, default: bool) -> bool:
        v = d.get(key, default)
        if not isinstance(v, bool):
            self.err(f"{path}.{key}", "must be a boolean")
            return default
        return v


def _checks(ctx: _Ctx, raw: Any) -> tuple[Check, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        ctx.err("checks", "must be a list")
        return ()
    out: list[Check] = []
    for i, item in enumerate(raw):
        path = f"checks[{i}]"
        if isinstance(item, str) and item:
            out.append(Check(item))
        elif isinstance(item, dict):
            d = ctx.section(item, path, {"run", "soft"})
            run = ctx.string(d, "run", path, required=True)
            soft = ctx.boolean(d, "soft", path, False)
            if run:
                out.append(Check(run, soft))
        else:
            ctx.err(path, "must be a string or a mapping with `run`")
    return tuple(out)


def _routes(ctx: _Ctx, raw: Any) -> tuple[Route, ...]:
    if raw is None:
        ctx.err("preview.routes", "missing")
        return ()
    if not isinstance(raw, list) or not raw:
        ctx.err("preview.routes", "must be a non-empty list")
        return ()
    out: list[Route] = []
    for i, item in enumerate(raw):
        path = f"preview.routes[{i}]"
        d = ctx.section(item, path, {"path", "to", "rewrite"}, required=True)
        p = ctx.string(d, "path", path, required=True)
        to = ctx.string(d, "to", path, required=True)
        if to is not None and not _TARGET.match(to):
            ctx.err(f"{path}.to", "must look like service:port")
            to = None
        rewrite = ctx.string(d, "rewrite", path)
        if p and to:
            out.append(Route(p, to, rewrite))
    return tuple(out)


def _overrides(ctx: _Ctx, raw: Any) -> dict[str, dict]:
    """``preview.overrides``: service name -> raw compose service fragment."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and k and isinstance(v, dict) for k, v in raw.items()
    ):
        ctx.err("preview.overrides", "must be a mapping of service name to mapping")
        return {}
    return {k: dict(v) for k, v in raw.items()}


_KEY = re.compile(r"^[a-z_]+$")


def _reset(ctx: _Ctx, raw: Any) -> dict[str, tuple[str, ...]]:
    """``preview.reset``: service name -> service keys reset with ``!reset``."""
    if raw is None:
        return {}
    ok = isinstance(raw, dict) and all(
        isinstance(k, str) and k and isinstance(v, list)
        and all(isinstance(i, str) and _KEY.match(i) for i in v)
        for k, v in raw.items()
    )
    if not ok:
        ctx.err("preview.reset", "must be a mapping of service name to a list of service keys")
        return {}
    return {k: tuple(v) for k, v in raw.items()}


def _preview(ctx: _Ctx, raw: Any) -> Preview:
    allowed = {"compose", "project", "build", "images", "overrides", "reset", "drop",
               "env_example", "env", "infra", "seed", "routes", "ready", "auth"}
    d = ctx.section(raw, "preview", allowed, required=True)
    auth = ctx.string(d, "auth", "preview", default="basic")
    if auth not in _AUTH:
        ctx.err("preview.auth", f"must be one of {', '.join(_AUTH)}")
        auth = "basic"
    return Preview(
        compose=ctx.string(d, "compose", "preview", default="", required=True) or "",
        project=ctx.string(d, "project", "preview", default="", required=True) or "",
        build=ctx.str_map(d, "build", "preview"),
        images=ctx.str_map(d, "images", "preview"),
        overrides=_overrides(ctx, d.get("overrides")),
        reset=_reset(ctx, d.get("reset")),
        drop=ctx.strings(d, "drop", "preview"),
        env_example=ctx.string(d, "env_example", "preview"),
        env=ctx.str_map(d, "env", "preview"),
        infra=ctx.strings(d, "infra", "preview"),
        seed=ctx.string(d, "seed", "preview"),
        routes=_routes(ctx, d.get("routes")),
        ready=ctx.string(d, "ready", "preview", default="https://${PREVIEW_HOST}/") or "",
        auth=auth,
    )


def parse_config(data: dict) -> Config:
    ctx = _Ctx()
    top = {"version", "label", "branch_prefix", "droplet", "limits", "setup",
           "checks_setup", "checks", "preview", "design", "allowed_labelers"}
    if not isinstance(data, dict):
        raise ConfigError("<root>: must be a mapping")
    d = ctx.section(data, "", top)

    if d.get("version") != 1 or isinstance(d.get("version"), bool):
        ctx.err("version", f"must be 1, got {d.get('version')!r}")

    dd = ctx.section(d.get("droplet"), "droplet", {"size", "region", "image"})
    droplet = Droplet(
        ctx.string(dd, "size", "droplet", Droplet.size),
        ctx.string(dd, "region", "droplet", Droplet.region),
        ctx.string(dd, "image", "droplet", Droplet.image),
    )
    ld = ctx.section(d.get("limits"), "limits", set(Limits.__dataclass_fields__))
    limits = Limits(**{
        name: ctx.integer(ld, name, "limits", getattr(Limits, name))
        for name in Limits.__dataclass_fields__
    })
    gd = ctx.section(d.get("design"), "design", {"figma", "paths"})
    design = Design(ctx.boolean(gd, "figma", "design", False), ctx.strings(gd, "paths", "design"))

    cfg = Config(
        version=1,
        label=ctx.string(d, "label", "", "blacksmith"),
        branch_prefix=ctx.string(d, "branch_prefix", "", "blacksmith/issue-"),
        droplet=droplet,
        limits=limits,
        setup=ctx.strings(d, "setup", ""),
        checks_setup=ctx.strings(d, "checks_setup", ""),
        checks=_checks(ctx, d.get("checks")),
        preview=_preview(ctx, d.get("preview")),
        design=design,
        allowed_labelers=ctx.strings(d, "allowed_labelers", ""),
    )
    if ctx.errors:
        raise ConfigError("\n".join(ctx.errors))
    return cfg


def load_config(path: Path) -> Config:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return parse_config(data)
