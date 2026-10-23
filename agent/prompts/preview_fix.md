Use the blacksmith-build skill.

The preview environment for issue #{issue} (branch `{branch}`, host `{host}`) failed to build
or boot. Fix only that problem.
You run unattended: never ask a human a question and never wait for an answer.

Tokens and passwords exist only as environment variables. Never print, commit, or paste them anywhere.

## Diagnostics from the runner

```
{diagnostics}
```

## Read first

- The diagnostics above: the error and the tail of the build and container logs.
- `{work_dir}/plan.md` for context on what was built.
- The repository's Dockerfiles, compose files and start scripts the diagnostics point at.

## Do

- Find the root cause of the build or boot failure and fix it in the repository.
- Change nothing that is not needed for the preview to build and start.
- Commit the fix with `git commit`. Do not start the preview yourself; the runner redeploys.

## Rules

- Never push; the runner pushes.
- `{work_dir}` is git-ignored scratch space and must never be committed.
- Output: a commit on `{branch}`. Append one line on what you fixed to `{work_dir}/notes.md`.
