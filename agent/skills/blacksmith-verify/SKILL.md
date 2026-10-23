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
3. The preview URL and basic-auth credentials from the prompt.

## Method

1. Checks: any required check marked FAIL means the result is fail. To explain a
   failure you may run the repository's own test command for that part, read-only.
2. Browser: use the binary named by `$BLACKSMITH_BROWSER`, headless:
   `"$BLACKSMITH_BROWSER" --headless=new --no-sandbox --disable-gpu --window-size=1366,900`
   plus `--screenshot=<file> <url>` for an image or `--dump-dom <url>` for the HTML.
   With basic auth, put credentials in the URL: `https://user:password@host/path`.
   Never write those credentials into the report.
3. For each acceptance criterion, in order:
   - open the page or call the endpoint that shows it (use `curl -u` for APIs);
   - take a screenshot named `<nn>-<slug>.png` in the shots directory, `nn` from 01;
   - for narrow layouts add `--window-size=390,844` and a `-mobile` slug;
   - compare what you see with the criterion; record pass or fail and why.
4. Look at what the plan did not mention but the change touches: broken pages, error
   banners, missing assets, 500s in dumped DOM. Record them as findings.
5. Look at screenshots yourself (read the image files) before judging them.

## Report

First line exactly `RESULT: pass` or `RESULT: fail`, then `## Checks`, `## Browser walk`,
`## Findings`. Fail if any required check failed or any criterion is not met.
Soft check failures and cosmetic issues are findings, not failures.
Each finding: where, what you expected, what you saw, how to reproduce, screenshot name.

## Done means

- The report exists with the exact first line and the three sections.
- Every acceptance criterion has a verdict and, for anything visible, a screenshot.
- No repository file changed.

## When stuck

- Preview unreachable or `none`: say so under Browser walk, judge on checks and code
  reading, and fail any criterion that only the browser could show.
- Browser crashes: retry once with a fresh `--user-data-dir` in the scratch directory;
  then fall back to `curl` and record that screenshots are missing.
