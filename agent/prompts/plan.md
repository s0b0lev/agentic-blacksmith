Use the blacksmith-plan skill.

You are planning work on GitHub issue #{issue} in this repository, on branch `{branch}`.
You run unattended: never ask a human a question and never wait for an answer.

Tokens and passwords exist only as environment variables. Never print, commit, or paste them anywhere.

## Read first

- `{work_dir}/issue.md`: the issue title, body and comments.
- The repository's `CLAUDE.md` files, README, and the code the issue touches.

## Write

Write exactly one file: `{work_dir}/plan.md`, with these sections in this order:

- `## Goal`: one short paragraph, in your own words, of what the issue wants.
- `## Acceptance criteria`: a Markdown checklist (`- [ ] ...`). Each item is observable:
  a behaviour a test can assert, or a state a browser screenshot can show.
- `## Files likely touched`: a bullet list of repository paths, each with a few words on why.
- `## Open questions`: every ambiguity you found, each followed by the answer you chose
  (`-> chosen: ...`). Pick the most reasonable reading; never block on a question.

## Rules

- Do not change any repository file in this stage. Only write `{work_dir}/plan.md`.
- `{work_dir}` is git-ignored scratch space and must never be committed.
- The preview will be served at `https://{host}/`; mention it only if a criterion needs a URL.
- Stop once `{work_dir}/plan.md` is written.
