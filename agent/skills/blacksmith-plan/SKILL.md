---
name: blacksmith-plan
description: Use when turning a GitHub issue into an implementation plan with testable acceptance criteria, unattended, before any code is written.
---

# Blacksmith: plan

You turn one issue into a plan another agent can build and a third can verify.
Nobody will answer questions. Every ambiguity is resolved by you, in writing.

## Read first

1. The issue file the prompt names: title, body, every comment. Later comments by the
   issue author override earlier text.
2. Every `CLAUDE.md` and the README. Note the project's layout, languages, test commands.
3. The code the issue is about. Search for the names, routes, components and models it
   mentions. Read enough to know which files will change and how they are tested.
4. Recent history of those files (`git log --oneline -- <path>`) for conventions.

## Method

1. State the goal in two to four sentences, in your own words. If you cannot, read more.
2. Write acceptance criteria as a checklist. Each item must be observable:
   - backend: a request and the expected response or stored state, or a test name;
   - frontend: a page or state and what is visible on it (this becomes a screenshot);
   - one behaviour per item; no "works correctly", "is clean", "is fast".
   Include the edge cases the issue implies (empty, error, permission, mobile width if UI).
3. List files likely touched, with a reason each. Include the test files.
4. List open questions. For each, choose the most reasonable reading given the codebase
   and the issue's intent, and write `-> chosen: ...`. Prefer the smaller change, existing
   patterns, and not breaking current behaviour.
5. Keep scope to the issue. Note tempting extras under open questions as "not doing".

## Done means

- The plan file exists with the four sections the prompt names, in order.
- Every criterion can be checked by a test or a screenshot without asking anyone.
- No repository file changed.

## When stuck

- Issue is vague: plan the smallest change that clearly satisfies its title, and say so.
- Issue asks for something impossible here (missing service, credentials): plan what can
  be done, mark the rest as an open question with the reason.
- Never write secrets, tokens or passwords into the plan, even if the issue contains them.
