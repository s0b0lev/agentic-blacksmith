Use the blacksmith-build skill.

Verification of issue #{issue} on branch `{branch}` failed. Fix what it found.
You run unattended: never ask a human a question and never wait for an answer.

Tokens and passwords exist only as environment variables. Never print, commit, or paste them anywhere.

## Check results (run by the runner)

{check_results}

## Verification report

{verify}

## Read first

- The check results and verification report above; the report is also in
  `{work_dir}/verify.md`, with screenshots in `{work_dir}/shots/`.
- `{work_dir}/plan.md` for the acceptance criteria.

## Do

- Fix every required check that failed and every finding marked as a failure.
- For each failure, reproduce it first (run the failing test or command), then fix the cause,
  then confirm it passes. Use a systematic-debugging skill if one is available.
- Commit with `git commit`. The runner pushes, redeploys the preview and verifies again.

## Rules

- Never push; never weaken, skip or delete a test to make it pass.
- `{work_dir}` is git-ignored scratch space and must never be committed.
- Output: commits on `{branch}`. Append one line per fix to `{work_dir}/notes.md`.
