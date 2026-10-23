Use the blacksmith-pr skill.

You are writing the pull request description for issue #{issue} (branch `{branch}`).
You run unattended: never ask a human a question and never wait for an answer.

Tokens and passwords exist only as environment variables. Never print, commit, or paste them anywhere.

## Read first

- `{work_dir}/issue.md`, `{work_dir}/plan.md`, `{work_dir}/verify.md`, `{work_dir}/design.md`,
  and `{work_dir}/notes.md` if it exists.
- `git log --oneline` and `git diff` against the base branch, to see what actually changed.
- The screenshots in `{work_dir}/shots/`.

## Write

Write exactly one file, `{work_dir}/pr.md`:

- Line 1: the title, at most 70 characters, no leading `#`, derived from the issue title.
- Line 2: blank.
- Then the body:
  - `Closes #{issue}`
  - `## Summary`: what changed and why, as short bullets a reviewer can check against the diff.
    Embed the most useful screenshots as `![<caption>](shots/<file>.png)`; the runner uploads
    them and rewrites the links.
  - `## What I was unsure about`: every assumption from the plan's open questions, every
    finding left open in verify or design, and anything you would ask the reviewer to look at.
    Write `Nothing.` only if that is true.

The runner appends the preview URL, verification and design reports; do not repeat them.

## Rules

- Do not change repository files and do not commit; never run `gh` and never push.
- `{work_dir}` is git-ignored scratch space and must never be committed.
- Stop once `{work_dir}/pr.md` is written.
