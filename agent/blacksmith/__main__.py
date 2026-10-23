import argparse
import dataclasses
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from blacksmith import __version__

DEFAULT_CONFIG_FILE = "/etc/blacksmith/config"


def read_config_file(path: Path) -> dict[str, str]:
    """Parse shell-style ``KEY=value`` lines, stripping one layer of quotes."""
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def context_from(conf: Mapping[str, str], environ: Mapping[str, str]):
    from blacksmith.stages import RunContext

    repo_root = Path(conf["REPO_ROOT"])
    return RunContext(
        repo_root=repo_root,
        work_dir=repo_root / ".blacksmith",
        issue=int(conf["ISSUE"]),
        branch=conf["BRANCH"],
        base_branch=conf["BASE_BRANCH"],
        host=conf["PREVIEW_HOST"],
        prompts_dir=Path(conf["PROMPTS_DIR"]),
        env=dict(environ),
    )


def _load_config(conf: dict[str, str], ctx):
    """The adopting repo's blacksmith.yml, or None after reporting why it is invalid."""
    from blacksmith.config import ConfigError, load_config

    try:
        return load_config(ctx.repo_root / conf["CONFIG_PATH"])
    except ConfigError as exc:
        print(f"blacksmith.yml is invalid:\n{exc}", file=sys.stderr)
        return None


def _github(conf: dict[str, str]):
    from blacksmith.github import GitHub
    from blacksmith.runner import subprocess_runner
    from blacksmith.scrub import load_secret_values

    return GitHub(conf["REPO"], subprocess_runner, load_secret_values(os.environ))


def _cmd_run(conf: dict[str, str], ctx) -> int:
    from blacksmith.runner import subprocess_runner
    from blacksmith.stages import run_first_pass

    cfg = _load_config(conf, ctx)
    if cfg is None:
        return 2
    state = run_first_pass(cfg, ctx, _github(conf), subprocess_runner)
    print(f"finished: stage={state.stage} pr={state.pr_url}")
    return 0  # salvage is a normal outcome; systemd must not restart the run


def _cmd_preview(conf: dict[str, str], ctx) -> int:
    from blacksmith.preview import PreviewContext, deploy
    from blacksmith.runner import subprocess_runner

    cfg = _load_config(conf, ctx)
    if cfg is None:
        return 2
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    res = deploy(cfg, PreviewContext(ctx.repo_root, ctx.host, ctx.work_dir), subprocess_runner)
    if not res.ok:
        print(f"preview failed: {res.error}\n{res.diagnostics}", file=sys.stderr)
        return 1
    print((ctx.work_dir / "preview.json").read_text())
    return 0


def _cmd_status(conf: dict[str, str], ctx) -> int:
    path = ctx.work_dir / "state.json"
    print(path.read_text().rstrip() if path.exists() else "no state")
    return 0


def _cmd_poll(conf: dict[str, str], ctx) -> int:
    from blacksmith.review import review_round
    from blacksmith.runner import subprocess_runner

    ran = False
    cfg = _load_config(conf, ctx)
    if cfg is not None:
        try:
            ran = review_round(cfg, ctx, _github(conf), subprocess_runner)
        except Exception as exc:  # the timer polls again in a minute
            print(f"poll failed: {exc!r}", file=sys.stderr)
    print(f"round: {'yes' if ran else 'no'}")
    return 0


def _cmd_self_destruct(conf: dict[str, str], ctx) -> int:
    from blacksmith.review import self_destruct

    cfg = _load_config(conf, ctx)  # None still self-destructs, with default limits
    try:
        self_destruct(cfg, ctx, _github(conf))
    except Exception as exc:
        print(f"self-destruct dispatch failed: {exc!r}", file=sys.stderr)
        return 1
    print("self-destruct requested")
    return 0


class UnknownKey(KeyError):
    pass


def config_value(cfg, dotted: str):
    """Walk dataclass attributes (and mapping keys) of ``cfg`` by a dotted key."""
    value = cfg
    for part in dotted.split("."):
        if dataclasses.is_dataclass(value) and not isinstance(value, type) \
                and part in {f.name for f in dataclasses.fields(value)}:
            value = getattr(value, part)
        elif isinstance(value, Mapping) and part in value:
            value = value[part]
        else:
            raise UnknownKey(dotted)
    return value


def _format_item(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if hasattr(value, "run") and dataclasses.is_dataclass(value):  # a Check
        return value.run
    if dataclasses.is_dataclass(value):
        return json.dumps(dataclasses.asdict(value))
    return str(value)


def format_config_value(value) -> str:
    """Scalars as text, booleans as true/false, sequences and mappings one item per line."""
    if isinstance(value, Mapping):
        return "".join(f"{k}={_format_item(v)}\n" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "".join(f"{_format_item(v)}\n" for v in value)
    if dataclasses.is_dataclass(value) and not hasattr(value, "run"):
        return json.dumps(dataclasses.asdict(value)) + "\n"
    return _format_item(value) + "\n"


def _cmd_config(conf: dict[str, str], args) -> int:
    from blacksmith.config import ConfigError, load_config

    path = args.config or Path(conf["REPO_ROOT"]) / conf["CONFIG_PATH"]
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        print(f"blacksmith.yml is invalid:\n{exc}", file=sys.stderr)
        return 2
    try:
        value = config_value(cfg, args.get)
    except UnknownKey:
        print(f"unknown config key: {args.get}", file=sys.stderr)
        return 3
    sys.stdout.write(format_config_value(value))
    return 0


def _cmd_fetch_issue(conf: dict[str, str], args) -> int:
    from blacksmith.stages import write_issue_markdown

    work_dir = Path(conf["REPO_ROOT"]) / ".blacksmith"
    print(write_issue_markdown(_github(conf), int(conf["ISSUE"]), work_dir))
    return 0


# Commands that run before bootstrap has written the full runner config: they get the
# parsed arguments instead of a RunContext.
EARLY_COMMANDS = {
    "config": (_cmd_config, "print a blacksmith.yml value by dotted key"),
    "fetch-issue": (_cmd_fetch_issue, "write .blacksmith/issue.md from the GitHub issue"),
}

COMMANDS = {
    "run": (_cmd_run, "run the first pass: plan, build, preview, verify, design, pr"),
    "preview": (_cmd_preview, "deploy the preview stack only and print preview.json"),
    "status": (_cmd_status, "print the runner state"),
    "poll": (_cmd_poll, "run a review round if the PR has new human comments"),
    "review-round": (_cmd_poll, "alias of poll"),
    "self-destruct": (_cmd_self_destruct, "post the expiry notice and request droplet teardown"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blacksmith")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config-file", type=Path, default=Path(DEFAULT_CONFIG_FILE),
                        help=f"KEY=value runner config (default {DEFAULT_CONFIG_FILE})")
    sub = parser.add_subparsers(dest="command")
    for name, (_, help_text) in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    cfg_parser = sub.add_parser("config", help=EARLY_COMMANDS["config"][1])
    cfg_parser.add_argument("--get", required=True, metavar="DOTTED.KEY")
    cfg_parser.add_argument("--config", type=Path, default=None,
                            help="blacksmith.yml path (default REPO_ROOT/CONFIG_PATH)")
    sub.add_parser("fetch-issue", help=EARLY_COMMANDS["fetch-issue"][1])
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command in EARLY_COMMANDS:
        early = args.command == "config" and args.config is not None
        conf = {} if early else read_config_file(args.config_file)
        return EARLY_COMMANDS[args.command][0](conf, args)
    conf = read_config_file(args.config_file)
    return COMMANDS[args.command][0](conf, context_from(conf, os.environ))


if __name__ == "__main__":
    sys.exit(main())
