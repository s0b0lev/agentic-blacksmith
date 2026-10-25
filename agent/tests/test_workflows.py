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


def test_spin_up_gates_on_allowed_labelers_or_write_permission():
    doc = _load(SPIN_UP)
    steps = doc["jobs"]["spin-up"]["steps"]
    names = [s.get("name") for s in steps]
    read = steps[names.index("Read config")]["run"]
    assert "get allowed_labelers" in read and "ALLOWED_LABELERS=" in read
    check = steps[names.index("Confirm issue is open and labeled")]
    assert check["id"] == "check"
    assert check["env"]["ACTOR"] == "${{ github.actor }}"
    script = check["run"]
    assert "$ALLOWED_LABELERS" in script and "not in allowed_labelers" in script
    assert 'gh api "repos/$REPO/collaborators/$ACTOR/permission" --jq .permission' in script
    assert "admin|maintain|write" in script and "::error::" in script
    assert names.index("Confirm issue is open and labeled") < names.index("Terraform apply")
    # github.actor is untrusted text: it may appear only as an env: mapping value.
    offenders = [line for line in _run_lines(doc) if "github.actor" in line]
    assert not offenders, f"github.actor referenced in run: {offenders}"
    env_values = [v for job in doc["jobs"].values() for st in job["steps"]
                  for v in (st.get("env") or {}).values()]
    in_env = sum("github.actor" in str(v) for v in env_values)
    assert in_env >= 1 and SPIN_UP.read_text().count("github.actor") == in_env


def test_adopter_example_uses_pinned_template():
    jobs = _load(ADOPTER)["jobs"]
    uses = [job["uses"] for job in jobs.values() if "uses" in job]
    assert sorted(u.split("@")[0] for u in uses) == [
        "s0b0lev/agentic-blacksmith/.github/workflows/spin-up.yml",
        "s0b0lev/agentic-blacksmith/.github/workflows/tear-down.yml",
    ]
    assert all(u.endswith("@v1") for u in uses), uses


def test_adopter_example_maps_secrets_explicitly():
    # `secrets: inherit` only works within one organization or enterprise; the adopter
    # and the template live in different personal accounts.
    jobs = _load(ADOPTER)["jobs"]
    callers = [job for job in jobs.values() if "uses" in job]
    assert len(callers) == 2
    for job in callers:
        secrets = job.get("secrets")
        assert isinstance(secrets, dict), f"expected an explicit secrets mapping, got {secrets!r}"
        for name in REQUIRED_SECRETS | {"FIGMA_API_KEY", "TELEGRAM_BOT_TOKEN"}:
            assert secrets.get(name) == f"${{{{ secrets.{name} }}}}", name


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

    assert "needs.resolve.outputs.issue != ''" in tear_if

    resolve = yaml.safe_dump(jobs["resolve"])
    for source in ("github.event.issue.number", "blacksmith/issue-", "client_payload.issue"):
        assert source in resolve, source


def test_adopter_unlabel_skips_tear_down_when_pr_is_open():
    resolve = _load(ADOPTER)["jobs"]["resolve"]
    assert resolve["permissions"] == {"pull-requests": "read"}
    step = resolve["steps"][0]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    script = step["run"]
    assert ('gh pr list --repo "$REPO" --head "blacksmith/issue-$n" --state open '
            "--json number --jq length") in script
    skip = script.index('echo "issue=" >> "$GITHUB_OUTPUT"')
    assert script.index('if [ "$EVENT_NAME" = issues ]') < skip
    assert skip < script.index('echo "issue=$n" >> "$GITHUB_OUTPUT"')


@pytest.mark.parametrize("path", [SPIN_UP, TEAR_DOWN], ids=lambda p: p.name)
def test_terraform_version_pinned(path):
    steps = [s for job in _load(path)["jobs"].values() for s in job["steps"]]
    setup = next(s for s in steps if str(s.get("uses", "")).startswith("hashicorp/setup-terraform"))
    assert setup["with"]["terraform_version"] == "1.9.8"


@pytest.mark.parametrize("path", [SPIN_UP, TEAR_DOWN], ids=lambda p: p.name)
def test_reusable_workflows_run_bash_with_pipefail(path):
    assert _load(path).get("defaults", {}).get("run", {}).get("shell") == "bash"


def test_tear_down_skips_only_when_workspace_is_absent():
    steps = _load(TEAR_DOWN)["jobs"]["tear-down"]["steps"]
    script = next(s["run"] for s in steps if "workspace select" in s.get("run", "")
                  and "default" not in s["run"])
    assert "terraform workspace list" in script
    select = next(line for line in script.splitlines() if "workspace select" in line)
    assert select.strip() == 'terraform workspace select "issue-$ISSUE"', select
    assert "||" not in select and not select.lstrip().startswith(("if ", "!"))


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


@pytest.mark.parametrize("path", [SPIN_UP, TEAR_DOWN], ids=lambda p: p.name)
def test_telegram_is_optional_and_reaches_terraform(path):
    call = _on(_load(path))["workflow_call"]
    assert not call["secrets"]["TELEGRAM_BOT_TOKEN"].get("required", False)
    assert call["inputs"]["telegram_chat_id"]["default"] == ""
    text = path.read_text()
    assert "TF_VAR_telegram_bot_token: ${{ secrets.TELEGRAM_BOT_TOKEN }}" in text
    assert "TF_VAR_telegram_chat_id: ${{ inputs.telegram_chat_id }}" in text


def test_adopter_example_wires_telegram_into_both_calls():
    jobs = _load(ADOPTER)["jobs"]
    for name in ("spin-up", "tear-down"):
        assert jobs[name]["secrets"]["TELEGRAM_BOT_TOKEN"] == "${{ secrets.TELEGRAM_BOT_TOKEN }}"
        assert jobs[name]["with"]["telegram_chat_id"] == "${{ vars.BLACKSMITH_TELEGRAM_CHAT_ID }}"


@pytest.mark.parametrize("path", [SPIN_UP, TEAR_DOWN], ids=lambda p: p.name)
def test_grafana_secrets_are_optional(path):
    secrets = _on(_load(path))["workflow_call"]["secrets"]
    for name in ("GRAFANA_CLOUD_TOKEN", "GRAFANA_API_TOKEN"):
        assert name in secrets and not secrets[name].get("required", False), name


def test_spin_up_creates_dashboard_before_terraform_apply():
    steps = _load(SPIN_UP)["jobs"]["spin-up"]["steps"]
    names = [s.get("name") for s in steps]
    assert names.index("Grafana dashboard") < names.index("Terraform apply")
    assert names.index("Resolve base branch") < names.index("Grafana dashboard")
    # After "Terraform workspace" so a failed create can keep the issue's previous link
    # (DASHBOARD_URL is in the droplet user data; changing it would replace the droplet).
    assert names.index("Terraform workspace") < names.index("Grafana dashboard")
    step = steps[names.index("Grafana dashboard")]
    assert step["env"]["GRAFANA_API_TOKEN"] == "${{ secrets.GRAFANA_API_TOKEN }}"
    assert step["working-directory"] == "template/terraform"
    assert step["env"]["AWS_ACCESS_KEY_ID"] == "${{ secrets.SPACES_ACCESS_KEY_ID }}"
    assert step["env"]["AWS_SECRET_ACCESS_KEY"] == "${{ secrets.SPACES_SECRET_ACCESS_KEY }}"
    assert "terraform output -raw dashboard_url" in step["run"]
    assert "previous dashboard link" in step["run"]
    assert "python3 -m blacksmith dashboard create" in step["run"]
    assert "::warning::" in step["run"]
    assert "DASHBOARD_URL=" in step["run"]
    assert 'gh api "repos/$REPO" --jq .private' in step["run"]


def test_terraform_outputs_the_dashboard_url():
    text = (ROOT / "terraform" / "outputs.tf").read_text()
    assert 'output "dashboard_url"' in text and "var.dashboard_url" in text


def test_spin_up_notices_a_missing_push_token():
    steps = _load(SPIN_UP)["jobs"]["spin-up"]["steps"]
    step = {s.get("name"): s for s in steps}["Grafana dashboard"]
    assert step["env"]["GRAFANA_CLOUD_TOKEN"] == "${{ secrets.GRAFANA_CLOUD_TOKEN }}"
    run = step["run"]
    assert 'if [ "$OBS_ENABLED" = true ] && [ -z "$GRAFANA_CLOUD_TOKEN" ]' in run
    assert "::notice::observability.enabled is true but GRAFANA_CLOUD_TOKEN is not set" in run
    # The token is only ever tested for emptiness, never printed.
    assert not [ln for ln in run.splitlines() if "echo" in ln and "$GRAFANA_CLOUD_TOKEN" in ln]


def test_spin_up_passes_observability_values_and_dashboard_url_to_terraform():
    text = SPIN_UP.read_text()
    assert "TF_VAR_grafana_cloud_token: ${{ secrets.GRAFANA_CLOUD_TOKEN }}" in text
    assert "TF_VAR_dashboard_url: ${{ env.DASHBOARD_URL }}" in text
    assert "TF_VAR_branch: ${{ env.BRANCH }}" in text
    assert "TF_VAR_observability_enabled: ${{ env.OBS_ENABLED }}" in text


def test_spin_up_comment_and_summary_mention_the_dashboard():
    steps = _load(SPIN_UP)["jobs"]["spin-up"]["steps"]
    by_name = {s.get("name"): s for s in steps}
    assert "Live dashboard" in by_name["Post issue comment"]["run"]
    assert "Live dashboard" in by_name["Summary"]["run"]


def test_tear_down_deletes_the_dashboard_best_effort():
    steps = _load(TEAR_DOWN)["jobs"]["tear-down"]["steps"]
    names = [s.get("name") for s in steps]
    assert names.index("Delete Grafana dashboard") > names.index("Terraform destroy")
    step = steps[names.index("Delete Grafana dashboard")]
    assert "python3 -m blacksmith dashboard delete" in step["run"]
    assert "::warning::" in step["run"]
    assert step["env"]["GRAFANA_API_TOKEN"] == "${{ secrets.GRAFANA_API_TOKEN }}"


def test_adopter_example_maps_grafana_secrets_into_both_calls():
    jobs = _load(ADOPTER)["jobs"]
    for name in ("spin-up", "tear-down"):
        for secret in ("GRAFANA_CLOUD_TOKEN", "GRAFANA_API_TOKEN"):
            assert jobs[name]["secrets"][secret] == f"${{{{ secrets.{secret} }}}}"
