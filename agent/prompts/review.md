Use the blacksmith-review skill.

You are addressing review comments on pull request #{pr_number} ({pr_url}) for issue #{issue},
branch `{branch}`. You run unattended: never ask a human a question and never wait for an answer.

Tokens and passwords exist only as environment variables. Never print, commit, or paste them anywhere.

## New comments

{comments}

## Read first

- The comments above, then the code each one points at.
- `{work_dir}/plan.md` and `{work_dir}/issue.md` for what the change is meant to do.
- The repository's `CLAUDE.md` files.

## Do

For each comment, either make the change it asks for or decide, with a reason, not to.
- Make the change, run the relevant tests, and `git commit` it. Note the short sha
  (`git rev-parse --short HEAD`).
- Decline only when the request is wrong, out of scope, or conflicts with the issue; say why.
- Comments that need no action (thanks, approvals) get no reply.

## Write

Write `{work_dir}/replies.json`: a JSON list with one object per comment you addressed or declined:

```json
[{"comment_id": 123, "kind": "review_comment", "body": "Done: renamed the helper. abc1234"}]
```

- `comment_id`: the integer after `comment_id` in the comment's header line.
- `kind`: `issue`, `review_comment` or `review`, exactly as in the header's parentheses.
- `body`: a short Markdown reply. When you changed code it ends with the commit sha.
- Do not add the marker `{reply_marker}`; the runner appends it and posts the replies.

## Rules

- Never run `gh` to comment, review, or push, and never `git push`. Commit only.
- `{work_dir}` is git-ignored scratch space and must never be committed.
- Stop once `{work_dir}/replies.json` is written.
