import re
from pathlib import Path

from blacksmith.stages import RunContext, load_prompt

AGENT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = AGENT / "prompts"
SKILLS_DIR = AGENT / "skills"
SECRET = ("Tokens and passwords exist only as environment variables. "
          "Never print, commit, or paste them anywhere.")
ROLES = {
    "plan": "blacksmith-plan", "build": "blacksmith-build", "preview_fix": "blacksmith-build",
    "verify": "blacksmith-verify", "build_fix": "blacksmith-build",
    "design": "blacksmith-design-check", "pr": "blacksmith-pr", "review": "blacksmith-review",
}
SKILLS = ("blacksmith-plan", "blacksmith-build", "blacksmith-verify",
          "blacksmith-design-check", "blacksmith-pr", "blacksmith-review")
EXTRA = {"diagnostics": "D", "check_results": "C", "verify": "V", "comments": "M",
         "pr_number": "12", "pr_url": "https://x/pull/12", "reply_marker": "<!-- r -->"}
KNOWN = ("issue", "branch", "host", "work_dir", "preview_url", "preview_auth", *EXTRA)
# Skills and prompts stay project-neutral: they must not borrow names from the sample example.
PROJECT_NAMES = ("sample-shop", "sampleshop", "acme")


def source(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


def render(tmp_path: Path, name: str) -> str:
    ctx = RunContext(repo_root=tmp_path, work_dir=tmp_path / ".blacksmith", issue=7,
                     branch="blacksmith/issue-7", base_branch="main", host="pr-7.example.com",
                     prompts_dir=PROMPTS_DIR, env={})
    return load_prompt(PROMPTS_DIR, name, ctx, EXTRA)


def test_all_prompts_exist():
    for name in ROLES:
        assert (PROMPTS_DIR / f"{name}.md").is_file(), name


def test_prompts_first_line_names_skill_and_are_short():
    for name, skill in ROLES.items():
        lines = source(name).splitlines()
        assert lines[0] == f"Use the {skill} skill.", name
        assert len(lines) <= 60, name


def test_prompts_have_secret_sentence():
    for name in ROLES:
        assert SECRET in source(name), name


def test_prompts_mention_work_dir_is_scratch():
    for name in ROLES:
        assert "never be committed" in source(name), name


def test_prompts_render_with_context(tmp_path):
    for name in ROLES:
        text = render(tmp_path, name)
        assert "{work_dir}" not in text, name
        assert str(tmp_path / ".blacksmith") in text, name


def test_prompts_do_not_use_unprovided_placeholders(tmp_path):
    for name in ROLES:
        assert "{checks}" not in source(name), name
        text = render(tmp_path, name)
        for key in KNOWN:
            assert "{" + key + "}" not in text, (name, key)


def test_verify_prompt_requires_result_line():
    text = source("verify")
    assert "RESULT: pass" in text and "RESULT: fail" in text
    assert "{work_dir}/verify.md" in text
    assert "{work_dir}/shots/" in text
    assert "$BLACKSMITH_BROWSER" in text
    for section in ("## Checks", "## Browser walk", "## Findings"):
        assert section in text


def test_plan_design_pr_prompts_name_sections():
    assert all(s in source("plan") for s in (
        "{work_dir}/plan.md", "## Goal", "## Acceptance criteria", "## Files likely touched",
        "## Open questions"))
    assert all(s in source("design") for s in (
        "{work_dir}/design.md", "## Compared", "## Fixed", "## Remaining",
        "Figma MCP not available in this session."))
    assert all(s in source("pr") for s in (
        "{work_dir}/pr.md", "Closes #{issue}", "## Summary", "## What I was unsure about",
        "](shots/"))


def test_review_prompt_forbids_gh_and_requires_replies_json():
    text = source("review")
    assert "{work_dir}/replies.json" in text
    assert re.search(r"[Nn]ever[^\n]{0,40}`?gh`?", text)
    for key in ("comment_id", "review_comment", "{comments}"):
        assert key in text


def test_skills_have_frontmatter_and_no_project_names():
    for skill in SKILLS:
        text = (SKILLS_DIR / skill / "SKILL.md").read_text()
        lines = text.splitlines()
        assert lines[0] == "---", skill
        end = lines.index("---", 1)
        front = lines[1:end]
        assert f"name: {skill}" in front, skill
        desc = [line for line in front if line.startswith("description: ")]
        assert len(desc) == 1 and desc[0].startswith("description: Use when"), skill
        assert len(lines[end + 1:]) <= 80, skill
        low = text.lower()
        assert not any(p in low for p in PROJECT_NAMES), skill


def test_prompts_have_no_project_names():
    for name in ROLES:
        low = source(name).lower()
        assert not any(p in low for p in PROJECT_NAMES), name
