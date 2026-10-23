Use the blacksmith-build skill.

You are implementing GitHub issue #{issue} on branch `{branch}` of this repository.
You run unattended: never ask a human a question and never wait for an answer.

Tokens and passwords exist only as environment variables. Never print, commit, or paste them anywhere.

## Read first

- `{work_dir}/issue.md`: the issue.
- `{work_dir}/plan.md`: the plan and its acceptance criteria. Build exactly that.
- Every `CLAUDE.md` in the repository and the skills the repository ships. Follow them.

## Do

- Implement every acceptance criterion in `{work_dir}/plan.md`.
- If test-driven-development or systematic-debugging skills are available, use them:
  a failing test first, then the code, then the passing test.
- Run the repository's own tests and linters for the parts you changed.
- Commit as you go with `git commit` (small, descriptive messages). Leave the tree clean.

## Rules

- Never push and never open a pull request; the runner does both.
- `{work_dir}` is git-ignored scratch space and must never be committed.
- Do not edit CI configuration, deployment secrets, or unrelated files.
- If something in the plan turns out impossible, do the closest reasonable thing and note it
  in `{work_dir}/notes.md` (create it if missing); do not stop to ask.
- Output: commits on `{branch}`, plus `{work_dir}/notes.md` only when you have notes.
