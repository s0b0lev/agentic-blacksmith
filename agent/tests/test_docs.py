import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from blacksmith.__main__ import COMMANDS, EARLY_COMMANDS

REPO = Path(__file__).resolve().parent.parent.parent
DOCS = REPO / "docs"
README = REPO / "README.md"
ADOPTING = DOCS / "adopting.md"
OPERATIONS = DOCS / "operations.md"
CHECKLIST = DOCS / "e2e-checklist.md"
CLEANUP = DOCS / "cleanup-droplets.sh"
DOC_FILES = (README, ADOPTING, OPERATIONS, CHECKLIST, CLEANUP)

SECRETS = (
    "BLACKSMITH_GITHUB_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "DIGITALOCEAN_TOKEN",
    "FIGMA_API_KEY",
    "SPACES_ACCESS_KEY_ID",
    "SPACES_SECRET_ACCESS_KEY",
)
VARIABLES = ("BLACKSMITH_STATE_BUCKET", "BLACKSMITH_PREVIEW_ZONE")


def test_cleanup_script_shellcheck():
    if shutil.which("uvx") is None:
        pytest.skip("uvx not installed")
    res = subprocess.run(
        ["uvx", "--from", "shellcheck-py", "shellcheck", "-S", "warning", str(CLEANUP)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_cleanup_script_dry_run_without_token_fails_clearly():
    env = {k: v for k, v in os.environ.items() if k != "DIGITALOCEAN_TOKEN"}
    res = subprocess.run(
        ["bash", str(CLEANUP), "--dry-run"], capture_output=True, text=True, env=env, timeout=30,
    )
    assert res.returncode != 0
    assert "DIGITALOCEAN_TOKEN" in res.stderr


def test_cleanup_script_documents_repo_filter_and_rejects_unknown_flags():
    help_res = subprocess.run(
        ["bash", str(CLEANUP), "--help"], capture_output=True, text=True, timeout=30,
    )
    assert help_res.returncode == 0
    assert "--repo" in help_res.stdout + help_res.stderr
    env = {**os.environ, "DIGITALOCEAN_TOKEN": "unused"}
    res = subprocess.run(
        ["bash", str(CLEANUP), "--dry-run", "--bogus"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert res.returncode != 0
    assert "--bogus" in res.stderr


def test_docs_exist_and_link():
    readme = README.read_text()
    assert "docs/adopting.md" in readme
    assert "docs/operations.md" in readme
    adopting = ADOPTING.read_text()
    for needle in ("fine-grained", "ns1.digitalocean.com", "setup-token", "preview.images",
                   *SECRETS, *VARIABLES):
        assert needle in adopting, needle
    operations = OPERATIONS.read_text()
    for needle in ("blacksmith-bootstrap.log", "journalctl -u blacksmith-run",
                   "blacksmith-self-destruct"):
        assert needle in operations, needle
    assert CHECKLIST.exists()


def test_docs_use_only_real_cli_commands():
    real = set(COMMANDS) | set(EARLY_COMMANDS)
    pattern = re.compile(r"(?:python3 -m blacksmith|run\.sh)\s+([a-z][a-z-]*)")
    seen = set()
    for path in DOC_FILES:
        for cmd in pattern.findall(path.read_text()):
            seen.add(cmd)
            assert cmd in real, f"{path.name}: unknown blacksmith command '{cmd}'"
    assert seen, "docs mention no blacksmith commands"


def test_docs_mention_no_placeholder_todos():
    for path in DOC_FILES:
        text = path.read_text()
        for word in ("TODO", "TBD"):
            assert word not in text, f"{path.name} contains {word}"
        assert "lorem" not in text.lower(), f"{path.name} contains lorem"
