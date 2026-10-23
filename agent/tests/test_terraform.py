import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
TF_DIR = ROOT / "terraform"
TEMPLATE = TF_DIR / "cloud-init.yaml.tftpl"

EXPECTED_VARIABLES = {
    "do_token",
    "repo",
    "issue_number",
    "base_branch",
    "label",
    "preview_zone",
    "config_path",
    "template_ref",
    "template_repo",
    "droplet_size",
    "droplet_region",
    "droplet_image",
    "claude_oauth_token",
    "github_token",
    "figma_api_key",
    "lifetime_hours",
}

NETWORK_HINTS = (
    "could not connect",
    "failed to query available provider packages",
    "no such host",
    "dial tcp",
    "i/o timeout",
    "connection refused",
    "network is unreachable",
    "tls handshake timeout",
)

needs_terraform = pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform is not installed")


def tf(*args: str, cwd: Path = TF_DIR) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["terraform", f"-chdir={cwd}", *args], capture_output=True, text=True, timeout=300
    )


def init_or_skip(*args: str, cwd: Path = TF_DIR) -> None:
    init = tf("init", "-input=false", "-no-color", *args, cwd=cwd)
    if init.returncode != 0:
        output = (init.stdout + init.stderr).lower()
        if any(hint in output for hint in NETWORK_HINTS):
            pytest.skip(f"terraform init needs network: {init.stderr.strip()}")
    assert init.returncode == 0, init.stdout + init.stderr


def module_console(tmp_path: Path, expr: str, **variables: str) -> subprocess.CompletedProcess:
    """Evaluate expr in a copy of the module with a local backend (console needs one)."""
    init_or_skip("-backend=false")
    work = tmp_path / "module"
    shutil.copytree(TF_DIR, work, ignore=shutil.ignore_patterns("terraform.tfstate*"))
    (work / "backend_override.tf").write_text('terraform {\n  backend "local" {}\n}\n')
    init_or_skip(cwd=work)
    base = {
        "repo": "o/r",
        "issue_number": "1",
        "template_ref": "main",
        "do_token": "x",
        "claude_oauth_token": "x",
        "github_token": "x",
    }
    base.update(variables)
    var_args = [arg for k, v in base.items() for arg in ("-var", f"{k}={v}")]
    return subprocess.run(
        ["terraform", f"-chdir={work}", "console", "-no-color", *var_args],
        input=f"{expr}\n",
        capture_output=True,
        text=True,
        timeout=120,
    )


@needs_terraform
def test_terraform_validate():
    init_or_skip("-backend=false")
    validate = tf("validate", "-no-color")
    assert validate.returncode == 0, validate.stdout + validate.stderr
    fmt = tf("fmt", "-check", "-recursive")
    assert fmt.returncode == 0, fmt.stdout + fmt.stderr


def test_cloud_init_renders():
    text = TEMPLATE.read_text()
    assert text.startswith("#cloud-config")
    for needle in ("/etc/blacksmith/env", "/etc/blacksmith/config", "bootstrap.sh", "${template_ref}", "${template_repo}"):
        assert needle in text, needle
    assert "blacksmith-bootstrap.log" not in text  # bootstrap.sh already tees into it


def test_variables_declared():
    declared = set(re.findall(r'^variable "([^"]+)"', (TF_DIR / "variables.tf").read_text(), re.M))
    assert declared == EXPECTED_VARIABLES


def test_lock_file_not_ignored():
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    assert ".terraform/" in gitignore
    assert not any(line.strip() in (".terraform.lock.hcl", "*.hcl") for line in gitignore)


@needs_terraform
def test_cloud_init_preserves_awkward_tokens(tmp_path):
    """Render the template through Terraform and check YAML keeps tokens byte-for-byte."""
    token = "sk-ant/oat01+ab==c'd\"e$f#g:h {i} ${j}"
    env = f"CLAUDE_CODE_OAUTH_TOKEN={token}\nBLACKSMITH_GITHUB_TOKEN=ghp_x/y+z=\nFIGMA_API_KEY="
    config = "ISSUE=7\nREPO=o/r\nBASE_BRANCH=main"
    vars_ = {
        "env_content": env,
        "config_content": config,
        "template_repo": "o/agentic-blacksmith",
        "template_ref": "v1.2.3",
    }
    (tmp_path / "vars.json").write_text(json.dumps(vars_))
    expr = f'templatefile({json.dumps(str(TEMPLATE))}, jsondecode(file("vars.json")))'
    result = subprocess.run(
        ["terraform", f"-chdir={tmp_path}", "console", "-no-color"],
        input=f"jsonencode({expr})\n",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rendered = json.loads(json.loads(result.stdout.strip()))
    doc = yaml.safe_load(rendered)
    files = {f["path"]: f for f in doc["write_files"]}
    assert files["/etc/blacksmith/env"]["content"] == env + "\n"
    assert files["/etc/blacksmith/env"]["permissions"] == "0600"
    assert files["/etc/blacksmith/env"]["owner"] == "root:root"
    assert files["/etc/blacksmith/config"]["content"] == config + "\n"
    assert files["/etc/blacksmith/config"]["permissions"] == "0644"
    commands = [c if isinstance(c, str) else " ".join(c) for c in doc["runcmd"]]
    joined = "\n".join(commands)
    assert "https://github.com/o/agentic-blacksmith/archive/v1.2.3.tar.gz" in joined
    assert "bash /opt/blacksmith/agent/bootstrap.sh" in joined


@needs_terraform
def test_repo_slug_is_a_valid_digitalocean_name(tmp_path):
    result = module_console(tmp_path, "local.repo_slug", repo="Owner/my_repo.site")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == '"owner-my-repo-site"'


@needs_terraform
@pytest.mark.parametrize(
    ("variables", "message"),
    [
        ({"issue_number": "1.5"}, "issue_number must be a positive whole number"),
        ({"lifetime_hours": "2.5"}, "lifetime_hours must be a whole number"),
        ({"repo": "owner/bad name"}, "repo must be owner/name"),
    ],
)
def test_variable_validation_rejects(tmp_path, variables, message):
    result = module_console(tmp_path, "local.repo_slug", **variables)
    # console exits 0 for some validation failures, so check the diagnostic itself
    output = " ".join((result.stdout + result.stderr).split())
    assert "Invalid value for variable" in output
    assert message in output
