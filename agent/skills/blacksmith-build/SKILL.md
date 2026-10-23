---
name: blacksmith-build
description: Use when implementing a written plan, fixing a broken preview build, or fixing failures from a verification report, unattended, committing but never pushing.
---

# Blacksmith: build

You change the repository so the plan's acceptance criteria hold, then leave a clean,
committed tree. Nobody will answer questions; the runner pushes and deploys.

## Read first

1. The plan (acceptance criteria) and the issue, from the scratch directory the prompt names.
2. Every `CLAUDE.md` in the repository, and any skills it ships. They override your habits.
3. The files the plan lists, their tests, and one similar existing feature to copy patterns from.
4. For a fix: the diagnostics or verification report in the prompt, before touching code.

## Method

1. Work criterion by criterion. If test-driven-development is available, use it:
   write the failing test, run it and see it fail, write the code, see it pass.
2. Match the project's style, structure and libraries. Do not add a dependency when the
   project already has something that does the job.
3. Run the relevant test suite and linter after each criterion, not only at the end.
4. Commit after each working step with a short message saying what and why.
   Never commit the scratch directory, build output, `.env` files or credentials.
5. Frontend work: keep existing components and design tokens; check the page renders
   without console errors if you can start it locally.
6. Backend work: add or update migrations when models change; keep APIs backward
   compatible unless the plan says otherwise.

## Fixing a failure (preview, checks, verification findings)

1. Reproduce: run the failing command or test and read the full error.
2. Find the root cause (use systematic-debugging if available). Do not guess-patch.
3. Fix the cause, re-run the reproduction, then the wider suite.
4. Never skip, delete or weaken a test, and never disable a linter rule, to get green.
5. Preview failures: change only what makes the build or boot work (Dockerfile, compose,
   start command, missing env default). Do not refactor.

## Done means

- Every acceptance criterion is implemented and covered by a test where testable.
- The project's tests and linters pass for what you changed.
- `git status` is clean; all work is committed; nothing is pushed.

## When stuck

- After two failed approaches to the same problem, stop, choose the simplest working
  variant, and write what you did and what is left to the notes file in the scratch directory.
- Missing credentials or external services: stub behind the project's existing mechanism
  (fixtures, mocks, env defaults) and note it. Never invent or print secrets.
