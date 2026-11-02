---
name: blacksmith-verify
description: Use when checking a built change against its acceptance criteria using the runner's check results and a headless browser walk of the preview, producing a pass/fail report with screenshots.
---

# Blacksmith: verify

You are the reviewer who did not write the code. Decide, with evidence, whether the
change meets the plan. You change no code and commit nothing.

## Read first

1. The plan's acceptance criteria and the issue.
2. The check results table from the prompt. The runner ran the checks; do not run them again.
3. The preview URL and the admin login from the prompt (user, password, login page).

## Method

1. Checks: any required check marked FAIL means the result is fail. To explain a
   failure you may run the repository's own test command for that part, read-only.
2. Browser: `node "$BLACKSMITH_BROWSE" --profile <scratch>/browser steps.json`
   runs a JSON list of steps (`goto`, `fill`, `click`, `wait`, `viewport`, `screenshot`,
   `dump`) in the binary named by `$BLACKSMITH_BROWSER`. The profile keeps cookies, so log
   in once and every later run is logged in. Never write credentials into the report.
3. List every screen the change touches: from the changed files in the prompt, map each
   route, page, component and template to the screens that render it, and add the states
   the acceptance criteria name. Screens behind a login count: log in through the real
   form with the admin login from the prompt, and walk them. When the prompt gives no admin
   login, find the development login the repository documents or seeds (README, docs, seed
   scripts, env examples). If no login can be found, say so in Findings.
4. For each screen and each criterion, in order:
   - open the page or call the endpoint that shows it (use `curl` for APIs);
   - take a screenshot named `<nn>-<slug>.png` in the shots directory, `nn` from 01;
   - for any screen whose layout the change can affect, add `{"viewport": "390x844"}` and a
     `-mobile` slug;
   - compare what you see with the criterion; record pass or fail and why.
5. Look at what the plan did not mention but the change touches: broken pages, error
   banners, missing assets, 500s in dumped DOM. Record them as findings.
6. Look at screenshots yourself (read the image files) before judging them.

## Report

First line exactly `RESULT: pass` or `RESULT: fail`, then `## Checks`, `## Browser walk`,
`## Findings`. Fail if any required check failed or any criterion is not met.
Soft check failures and cosmetic issues are findings, not failures.
Each finding: where, what you expected, what you saw, how to reproduce, screenshot name.

## Done means

- The report exists with the exact first line and the three sections.
- Every acceptance criterion has a verdict and, for anything visible, a screenshot.
- Every screen the change touches has a screenshot, logged in where the screen needs it.
- No repository file changed.

## When stuck

- Preview unreachable or `none`: say so under Browser walk, judge on checks and code
  reading, and fail any criterion that only the browser could show.
- Browser crashes: retry once with a fresh `--profile` directory; then fall back to
  `curl` and the bare `"$BLACKSMITH_BROWSER" --headless=new --screenshot=<file> <url>` for
  logged-out pages, and record which screenshots are missing.
