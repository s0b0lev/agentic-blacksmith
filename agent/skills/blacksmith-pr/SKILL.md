---
name: blacksmith-pr
description: Use when writing a pull request title and description for finished unattended work, summarising changes, evidence and open doubts for a human reviewer.
---

# Blacksmith: pull request

You write the description a busy reviewer reads first. It must be accurate to the diff
and honest about doubts. You change no code and commit nothing.

## Read first

1. The issue, plan, verification report, design report and notes, in the scratch directory.
2. `git log --oneline <base>..HEAD` and `git diff --stat <base>...HEAD`; open the diff of
   any file whose purpose is not obvious.
3. The list of screenshots in the shots directory.

## Method

1. Title: imperative, specific, at most 70 characters, from the issue title
   (e.g. "Add unit selector to the weight form"). No issue number, no `#`, no prefix tags.
2. Body starts with the closing keyword line the prompt gives.
3. Summary: three to eight bullets of what changed and why, grouped by area (backend,
   frontend, tests, config). Mention migrations, new dependencies and new env variables
   explicitly. Describe only what is in the diff.
4. Screenshots: embed the two to four that best show the result, as
   `![caption](shots/<file>.png)` with a caption saying what state is shown.
5. What I was unsure about: assumptions from the plan's open questions, findings left
   open in verification or design, notes from the build, anything worth a second look.
   One bullet each, specific enough to act on.
6. Keep it short. The runner appends preview links and the full reports.

## Done means

- The output file has the title on line 1, a blank line 2, then the body.
- Every claim in the summary is visible in the diff.
- Nothing secret appears: no tokens, passwords, preview credentials.

## When stuck

- Verification failed but the run reached this stage: say so at the top of the unsure
  section and list what failed.
- Reports missing: write the description from the diff and say which report was missing.
