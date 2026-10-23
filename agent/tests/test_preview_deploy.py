import json
from pathlib import Path

import pytest

from blacksmith.config import load_config
from blacksmith.preview import PreviewContext, deploy
from blacksmith.runner import CommandResult

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/sample-shop/blacksmith.yml"
HOST = "pr-7.example.com"
COMPOSE = """\
services:
  db: {image: postgres}
  minio: {image: minio/minio}
  minio-init: {image: minio/mc, depends_on: [minio]}
  redis: {image: redis}
  meilisearch: {image: getmeili/meilisearch}
  api: {image: api, depends_on: {db: {condition: service_healthy}}}
  worker: {image: api}
  beat: {image: api}
  admin: {image: admin}
  storefront: {image: storefront}
"""
ENV_EXAMPLE = "SECRET_KEY=change-me\nDEBUG=false\n"


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self):
        return self.now


@pytest.fixture
def cfg():
    return load_config(EXAMPLE)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "ops").mkdir(parents=True)
    (root / "ops/docker-compose.prod.yml").write_text(COMPOSE)
    (root / "ops/.env.prod.example").write_text(ENV_EXAMPLE)
    return root


@pytest.fixture
def ctx(repo, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return PreviewContext(repo_root=repo, host=HOST, work_dir=work)


def _run(cfg, ctx, fake_runner, status=lambda url, auth: 200, **kw):
    clock = kw.pop("clock", Clock())
    return deploy(
        cfg,
        ctx,
        fake_runner,
        http_get=status,
        sleep=clock.sleep,
        clock=clock,
        rand=lambda: "rnd",
        **kw,
    )


def _base(cfg, ctx):
    return [
        "docker", "compose", "-p", cfg.preview.project,
        "--project-directory", str(ctx.repo_root),
        "-f", str(ctx.repo_root / cfg.preview.compose),
        "-f", str(ctx.work_dir / "compose.override.yml"),
    ]  # fmt: skip


def test_deploy_command_sequence_jim_scale(cfg, ctx, fake_runner):
    fake_runner.responses["docker run"] = CommandResult(0, "HASH\n", "")
    result = _run(cfg, ctx, fake_runner)
    assert result.ok and result.error is None
    base = _base(cfg, ctx)
    compose = str(ctx.repo_root / cfg.preview.compose)
    override = str(ctx.work_dir / "compose.override.yml")
    seed = (
        "export COMPOSE_PROJECT_NAME=blacksmith-preview "
        f"COMPOSE_FILE={compose}:{override} COMPOSE_PATH_SEPARATOR=: "
        f"&& cd {ctx.repo_root} && {cfg.preview.seed}"
    )
    assert fake_runner.calls == [
        ["docker", "run", "--rm", "caddy:2", "caddy", "hash-password",
         "--plaintext", "rnd"],
        base + ["up", "-d", "--build", "--wait", "db", "minio", "redis", "meilisearch"],
        ["bash", "-lc", seed],
        base + ["up", "-d", "--build", "--wait"],
    ]  # fmt: skip
    assert all(k["cwd"] == ctx.repo_root for k in fake_runner.kwargs)
    assert [k.get("timeout") for k in fake_runner.kwargs] == [None, 1800, 900, 1800]
    for call in fake_runner.calls:
        if "-p" in call:
            assert call[call.index("-p") + 1] == "blacksmith-preview"
    caddyfile = (ctx.work_dir / "Caddyfile").read_text()
    assert f"{HOST} {{" in caddyfile and "preview HASH" in caddyfile
    assert str(ctx.work_dir / "Caddyfile") in (
        ctx.work_dir / "compose.override.yml"
    ).read_text()
    assert result.auth == ("preview", "rnd")
    assert result.url == f"https://{HOST}/"


def test_seed_runs_before_app_services(cfg, ctx, fake_runner):
    _run(cfg, ctx, fake_runner)
    kinds = ["seed" if c[0] == "bash" else "up" for c in fake_runner.calls if c[0] != "docker" or "up" in c]
    assert kinds == ["up", "seed", "up"]


def test_env_written_next_to_compose_file(cfg, ctx, fake_runner):
    result = _run(cfg, ctx, fake_runner)
    env_path = ctx.repo_root / ".env"
    text = env_path.read_text()
    assert "SECRET_KEY='rnd'" in text
    assert f"MINIO_PUBLIC_URL='https://{HOST}/media/product-images'" in text
    meta = json.loads((ctx.work_dir / "preview.json").read_text())
    assert meta == {
        "url": result.url,
        "host": HOST,
        "auth": ["preview", "rnd"],
        "env_path": str(env_path),
    }


def test_ready_poll_until_2xx(cfg, ctx, fake_runner):
    statuses = iter([502, 502, 200])
    clock = Clock()
    result = _run(cfg, ctx, fake_runner, status=lambda u, a: next(statuses), clock=clock)
    assert result.ok
    assert clock.sleeps == [5, 5]


def test_ready_probe_exception_counts_as_not_ready(cfg, ctx, fake_runner):
    calls = []

    def probe(url, auth):
        calls.append((url, auth))
        if len(calls) == 1:
            raise OSError("refused")
        return 301

    assert _run(cfg, ctx, fake_runner, status=probe).ok
    assert calls == [(f"https://{HOST}/", ("preview", "rnd"))] * 2


def test_ready_timeout_returns_diagnostics(cfg, ctx, fake_runner):
    fake_runner.responses[" ".join(_base(cfg, ctx) + ["ps"])] = CommandResult(0, "PS-OUT", "")
    fake_runner.responses[" ".join(_base(cfg, ctx) + ["logs"])] = CommandResult(0, "LOG-OUT", "")
    result = _run(cfg, ctx, fake_runner, status=lambda u, a: 502, ready_timeout_s=10)
    assert result.ok is False
    assert "ready probe timed out after 10s (last status 502)" in result.error
    assert "PS-OUT" in result.diagnostics and "LOG-OUT" in result.diagnostics
    assert not (ctx.work_dir / "preview.json").exists()


def test_failed_up_returns_error_without_seed(cfg, ctx, fake_runner):
    fake_runner.responses[" ".join(_base(cfg, ctx) + ["up"])] = CommandResult(1, "", "boom")
    result = _run(cfg, ctx, fake_runner)
    assert result.ok is False
    assert "boom" in result.diagnostics
    assert not any(c[0] == "bash" for c in fake_runner.calls)
    assert any(c[-1] == "ps" for c in fake_runner.calls)
    assert any(c[-3:] == ["logs", "--tail", "100"] for c in fake_runner.calls)


def test_failed_hash_brings_up_nothing(cfg, ctx, fake_runner):
    fake_runner.responses["docker run"] = CommandResult(1, "", "no docker")
    result = _run(cfg, ctx, fake_runner)
    assert result.ok is False and "no docker" in result.diagnostics
    assert len(fake_runner.calls) == 1


def test_no_auth_when_config_none(cfg, ctx, fake_runner):
    from dataclasses import replace

    cfg = replace(cfg, preview=replace(cfg.preview, auth="none"))
    result = _run(cfg, ctx, fake_runner)
    assert result.ok and result.auth is None
    assert not any(c[:2] == ["docker", "run"] for c in fake_runner.calls)
    assert "basic_auth" not in (ctx.work_dir / "Caddyfile").read_text()
    assert json.loads((ctx.work_dir / "preview.json").read_text())["auth"] is None


def test_no_seed_when_none(cfg, ctx, fake_runner):
    from dataclasses import replace

    cfg = replace(cfg, preview=replace(cfg.preview, seed=None))
    assert _run(cfg, ctx, fake_runner).ok
    assert not any(c[0] == "bash" for c in fake_runner.calls)


def test_deploy_refuses_dependency_on_dropped_service(cfg, ctx, fake_runner):
    compose = ctx.repo_root / cfg.preview.compose
    compose.write_text(COMPOSE.replace("worker: {image: api}", "worker: {image: api, depends_on: [minio-init]}"))
    result = _run(cfg, ctx, fake_runner)
    assert result.ok is False
    assert result.error == "service 'worker' depends_on dropped service 'minio-init'"
    assert result.diagnostics == ""
    assert fake_runner.calls == []
