import json
import shlex
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
        "-f", str(ctx.repo_root / cfg.preview.compose),
        "-f", str(ctx.work_dir / "compose.override.yml"),
        "-f", str(ctx.work_dir / "compose.extra.yml"),
    ]  # fmt: skip


def test_deploy_command_sequence_sample(cfg, ctx, fake_runner):
    fake_runner.responses["docker run"] = CommandResult(0, "HASH\n", "")
    result = _run(cfg, ctx, fake_runner)
    assert result.ok and result.error is None
    base = _base(cfg, ctx)
    compose = str(ctx.repo_root / cfg.preview.compose)
    override = str(ctx.work_dir / "compose.override.yml")
    extra = str(ctx.work_dir / "compose.extra.yml")
    seed = (
        "export COMPOSE_PROJECT_NAME=blacksmith-preview "
        f"COMPOSE_FILE={shlex.quote(compose + ':' + override + ':' + extra)} COMPOSE_PATH_SEPARATOR=: "
        f"&& cd {shlex.quote(str(ctx.repo_root / 'ops'))} && {cfg.preview.seed}"
    )
    assert fake_runner.calls == [
        ["docker", "run", "--rm", "caddy:2", "caddy", "hash-password",
         "--plaintext", "rnd"],
        base + ["up", "-d", "--build", "--wait", "db", "minio", "redis", "meilisearch"],
        ["bash", "-lc", seed],
        base + ["up", "-d", "--build", "--wait"],
    ]  # fmt: skip
    assert all(k["cwd"] == ctx.repo_root for k in fake_runner.kwargs)
    assert [k.get("timeout") for k in fake_runner.kwargs] == [300, 1800, 900, 1800]
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
    # Compose's project directory is the compose file's directory (ops/), so
    # .env lives there. bootstrap's `.env` line in .git/info/exclude has no
    # slash, so like a gitignore pattern it matches ops/.env at any depth.
    result = _run(cfg, ctx, fake_runner)
    assert not (ctx.repo_root / ".env").exists()
    env_path = ctx.repo_root / "ops" / ".env"
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


def test_override_build_contexts_are_absolute(cfg, ctx, fake_runner):
    _run(cfg, ctx, fake_runner)
    text = (ctx.work_dir / "compose.override.yml").read_text()
    contexts = [line.split(":", 1)[1].strip() for line in text.splitlines()
                if line.strip().startswith("context:")]
    assert contexts and all(Path(c).is_absolute() for c in contexts)
    assert str(ctx.repo_root / "api") in contexts


def test_seed_failure_returns_diagnostics(cfg, ctx, fake_runner):
    base = _base(cfg, ctx)
    fake_runner.responses["bash -lc"] = CommandResult(3, "", "seed exploded")
    fake_runner.responses[" ".join(base + ["ps"])] = CommandResult(0, "PS-OUT", "")
    fake_runner.responses[" ".join(base + ["logs"])] = CommandResult(0, "LOG-OUT", "")
    result = _run(cfg, ctx, fake_runner)
    assert result.ok is False
    assert result.error == "seed failed (exit 3)"
    assert "== seed ==\nseed exploded" in result.diagnostics
    assert "PS-OUT" in result.diagnostics and "LOG-OUT" in result.diagnostics
    assert base + ["up", "-d", "--build", "--wait"] not in fake_runner.calls
    assert not (ctx.work_dir / "preview.json").exists()


def test_final_up_failure_returns_diagnostics(cfg, ctx, fake_runner):
    base = _base(cfg, ctx)
    fake_runner.responses[" ".join(base + ["up", "-d", "--build", "--wait"]) + " db"] = (
        CommandResult(0, "", "")
    )
    fake_runner.responses[" ".join(base + ["up", "-d", "--build", "--wait"])] = (
        CommandResult(1, "", "api build failed")
    )
    fake_runner.responses[" ".join(base + ["ps"])] = CommandResult(0, "PS-OUT", "")
    fake_runner.responses[" ".join(base + ["logs"])] = CommandResult(0, "LOG-OUT", "")
    result = _run(cfg, ctx, fake_runner)
    assert result.ok is False
    assert result.error == "up failed (exit 1)"
    assert "== up ==\napi build failed" in result.diagnostics
    assert "PS-OUT" in result.diagnostics and "LOG-OUT" in result.diagnostics
    assert any(c[0] == "bash" for c in fake_runner.calls)  # seed ran first


def test_second_deploy_reuses_env_values_and_auth(cfg, ctx, fake_runner):
    fake_runner.responses["docker run"] = CommandResult(0, "HASH\n", "")
    counter = iter(range(100))

    def rand():
        return f"r{next(counter)}"

    def deploy_once():
        clock = Clock()
        return deploy(cfg, ctx, fake_runner, http_get=lambda u, a: 200,
                      sleep=clock.sleep, clock=clock, rand=rand)

    first = deploy_once()
    env1 = (ctx.repo_root / "ops/.env").read_text()
    caddy1 = (ctx.work_dir / "Caddyfile").read_text()
    calls_before = len(fake_runner.calls)
    second = deploy_once()
    assert first.ok and second.ok
    assert (ctx.repo_root / "ops/.env").read_text() == env1
    assert (ctx.work_dir / "Caddyfile").read_text() == caddy1
    assert second.auth == first.auth
    new_calls = fake_runner.calls[calls_before:]
    assert not any(c[:2] == ["docker", "run"] for c in new_calls)
    assert not any("--force-recreate" in c for c in new_calls)
    secrets = json.loads((ctx.work_dir / "preview-secrets.json").read_text())
    assert secrets["auth"] == [first.auth[0], first.auth[1], "HASH"]
    assert secrets["env_values"] == {"SECRET_KEY": "r1"}
    assert (ctx.work_dir / "preview-secrets.json").stat().st_mode & 0o777 == 0o600


def test_changed_caddyfile_recreates_caddy(cfg, ctx, fake_runner):
    from dataclasses import replace

    _run(cfg, ctx, fake_runner)
    assert not any("--force-recreate" in c for c in fake_runner.calls)
    fake_runner.calls.clear()
    moved = replace(ctx, host="pr-7.other.example.com")
    _run(cfg, moved, fake_runner)
    assert fake_runner.calls[-1] == _base(cfg, ctx) + [
        "up", "-d", "--no-deps", "--force-recreate", "caddy"]


def test_no_extra_file_without_overrides(cfg, ctx, fake_runner):
    from dataclasses import replace

    (ctx.work_dir / "compose.extra.yml").write_text("stale")
    cfg = replace(cfg, preview=replace(cfg.preview, overrides={}))
    assert _run(cfg, ctx, fake_runner).ok
    assert not (ctx.work_dir / "compose.extra.yml").exists()
    assert not any("compose.extra.yml" in " ".join(c) for c in fake_runner.calls)


def test_extra_file_written_and_passed_last(cfg, ctx, fake_runner):
    assert _run(cfg, ctx, fake_runner).ok
    assert "bitnamilegacy/minio" in (ctx.work_dir / "compose.extra.yml").read_text()
    up = fake_runner.calls[-1]
    files = [up[i + 1] for i, a in enumerate(up) if a == "-f"]
    assert files[-1] == str(ctx.work_dir / "compose.extra.yml")
