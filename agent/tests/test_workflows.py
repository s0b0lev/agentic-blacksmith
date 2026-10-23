import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
SPIN_UP = WORKFLOWS / "spin-up.yml"
TEAR_DOWN = WORKFLOWS / "tear-down.yml"
ADOPTER = ROOT / "examples" / "sample-shop" / ".github" / "workflows" / "blacksmith.yml"
VARIABLES_TF = ROOT / "terraform" / "variables.tf"

REQUIRED_SECRETS = {
    "DIGITALOCEAN_TOKEN",
    "SPACES_ACCESS_KEY_ID",
    "SPACES_SECRET_ACCESS_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "BLACKSMITH_GITHUB_TOKEN",
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _on(doc: dict) -> dict:
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    assert on is not None, "workflow has no `on:` section"
    return on


def _run_lines(doc: dict) -> list[str]:
    lines = []
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if "run" in step:
                lines.extend(step["run"].splitlines())
    return lines


@pytest.mark.parametrize("path", [SPIN_UP, TEAR_DOWN], ids=lambda p: p.name)
def test_workflows_are_valid_yaml_with_workflow_call(path):
    on = _on(_load(path))
    assert "workflow_call" in on
    assert on["workflow_call"]["inputs"]["issue"]["required"] is True
    assert on["workflow_call"]["inputs"]["state_bucket"]["required"] is True


@pytest.mark.parametrize("path", [SPIN_UP, TEAR_DOWN], ids=lambda p: p.name)
def test_spin_up_declares_required_secrets(path):
    secrets = _on(_load(path))["workflow_call"]["secrets"]
    for name in REQUIRED_SECRETS:
        assert secrets[name]["required"] is True, name
    assert "FIGMA_API_KEY" in secrets
    assert not secrets["FIGMA_API_KEY"].get("required", False)


@pytest.mark.parametrize("path", [SPIN_UP, TEAR_DOWN], ids=lambda p: p.name)
def test_spin_up_passes_every_terraform_variable(path):
    names = re.findall(r'^variable "([a-z_]+)"', VARIABLES_TF.read_text(), re.M)
    assert names, "no variables found in variables.tf"
    text = path.read_text()
    missing = [n for n in names if not re.search(rf"\bTF_VAR_{n}[:=]", text)]
    assert not missing, f"{path.name} does not set TF_VAR_ for {missing}"


@pytest.mark.parametrize("path", [SPIN_UP, TEAR_DOWN, ADOPTER], ids=lambda p: p.name)
def test_no_secret_on_command_line(path):
    offenders = [line for line in _run_lines(_load(path)) if "secrets." in line]
    assert not offenders, f"secrets referenced in run: {offenders}"


def test_adopter_example_uses_pinned_template():
    jobs = _load(ADOPTER)["jobs"]
    uses = [job["uses"] for job in jobs.values() if "uses" in job]
    assert sorted(u.split("@")[0] for u in uses) == [
        "s0b0lev/agentic-blacksmith/.github/workflows/spin-up.yml",
        "s0b0lev/agentic-blacksmith/.github/workflows/tear-down.yml",
    ]
    assert all(u.endswith("@v1") for u in uses), uses
    assert all(job.get("secrets") == "inherit" for job in jobs.values() if "uses" in job)


def test_adopter_example_routes_all_three_events():
    doc = _load(ADOPTER)
    on = _on(doc)
    assert set(on["issues"]["types"]) == {"labeled", "unlabeled"}
    assert on["pull_request"]["types"] == ["closed"]
    assert on["repository_dispatch"]["types"] == ["blacksmith-self-destruct"]

    jobs = doc["jobs"]
    spin_if = jobs["spin-up"]["if"]
    assert "'labeled'" in spin_if and "'blacksmith'" in spin_if

    tear_if = jobs["tear-down"]["if"]
    assert "'unlabeled'" in tear_if
    assert "'blacksmith/issue-'" in tear_if
    assert "repository_dispatch" in tear_if
    assert jobs["tear-down"]["needs"] == "resolve"
    assert "needs.resolve.outputs.issue" in str(jobs["tear-down"]["with"]["issue"])

    resolve = yaml.safe_dump(jobs["resolve"])
    for source in ("github.event.issue.number", "blacksmith/issue-", "client_payload.issue"):
        assert source in resolve, source


def test_actionlint():
    actionlint = shutil.which("actionlint")
    if actionlint is None:
        pytest.skip("actionlint not installed")
    result = subprocess.run(
        [actionlint, str(SPIN_UP), str(TEAR_DOWN), str(ADOPTER)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
