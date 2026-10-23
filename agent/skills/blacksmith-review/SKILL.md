---
name: blacksmith-review
description: Use when addressing human review comments on an open pull request unattended, committing changes and writing replies to a file for the runner to post.
---

# Blacksmith: review

You act on reviewer feedback. Each comment gets a change or a reasoned answer. The runner
posts your replies and pushes; you only commit and write the replies file.

## Read first

1. All new comments in the prompt. Note each header: author, kind, path and line, comment id.
2. The code at each referenced path and line, and the surrounding function.
3. The plan and issue, so you know what the change is for.
4. Every `CLAUDE.md`; reviewers usually expect its rules to be followed.

## Method

1. Group comments that ask for the same thing; handle the group once, reply to each.
2. For each request:
   - clear and in scope: make the change, run the related tests, commit with a message
     naming the request, record the short sha;
   - a question: answer it from the code; change code only if the answer reveals a bug;
   - wrong, out of scope, or conflicting with the issue: do not change code; explain why
     in one or two sentences, politely, and offer the alternative if there is one;
   - unclear: choose the most reasonable reading, do it, and say which reading you chose.
3. Conflicting comments from different reviewers: follow the later one by the issue
   author or repository maintainer; say so in both replies.
4. Comments with no request (approval, thanks): no reply.
5. Keep the tests and linters passing after every commit.

## Replies

A JSON list in the replies file the prompt names, one object per comment answered:
`comment_id` (int, from the header), `kind` (from the header), `body` (Markdown).
Bodies are short, say what was done, and end with the short commit sha when code changed.

## Done means

- Every actionable comment has a commit or a reply explaining why not.
- All changes are committed; `git status` is clean.
- The replies file is valid JSON (check with `python3 -m json.tool <file>`).

## Never

- Never run `gh` or call the GitHub API to comment, review, resolve or merge.
- Never push, force-push, rebase or amend published commits.
- Never paste tokens, passwords or preview credentials into code, commits or replies.
