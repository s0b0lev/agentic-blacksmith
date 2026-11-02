Use the blacksmith-verify skill.

You are verifying issue #{issue} on branch `{branch}` against its plan, in the running preview.
You run unattended: never ask a human a question and never wait for an answer.

Tokens and passwords exist only as environment variables. Never print, commit, or paste them anywhere.

## Inputs

- `{work_dir}/plan.md`: the acceptance criteria to verify. `{work_dir}/issue.md`: the issue.
- Check results. The runner already ran the configured checks; do not run them yourself.
  You may run the repository's own test commands only to investigate a failure.

{check_results}

- Preview: {preview_url}  (admin login `user / password`: {preview_login})
- Files the branch changes against the base branch:

{changed_files}

## Browser walk

Screenshot every screen the change touches, not only the states the acceptance criteria
name: map each changed route, page, component and template above to the screens that render
it, and open each one. Screens behind a login are not exempt: log in through the real form
with the admin login above (its login page when one is given, else the app's own). When the
admin login is `none`, use the development credentials the repository documents or seeds
(README, docs, seed scripts, env examples). Then walk the pages. Take the narrow layout too
(viewport `390x844`, `-mobile` slug) for any screen whose layout the change can affect.

The helper `node "$BLACKSMITH_BROWSE"` drives the browser (`$BLACKSMITH_BROWSER`) with a
persistent profile, so a login in one run carries over to the next:

```
node "$BLACKSMITH_BROWSE" --profile {work_dir}/browser steps.json
```

`steps.json` is a list of steps run in order: `{{"goto": url}}`, `{{"fill": ["css", "value"]}}`,
`{{"click": "css"}}`, `{{"wait": "css" | ms | "navigation"}}`, `{{"viewport": "390x844"}}`,
`{{"screenshot": "{work_dir}/shots/01-home.png"}}`, `{{"dump": "{work_dir}/page.html"}}`.
For a single logged-out page the bare browser also works: `"$BLACKSMITH_BROWSER"
--headless=new --no-sandbox --disable-gpu --window-size=1366,900 --screenshot=<file> <url>`.

Name screenshots `{work_dir}/shots/<nn>-<slug>.png` (`nn` counts from 01). Do not change
repository files and do not commit anything.

## Write

Write `{work_dir}/verify.md`. Its first line is exactly `RESULT: pass` or `RESULT: fail`.
`RESULT: fail` when any required check failed or any acceptance criterion is not met.
Then these sections:

- `## Checks`: the check results above, one line of interpretation per failure.
- `## Browser walk`: per acceptance criterion, what you opened, what you saw, the screenshot.
- `## Findings`: each problem as a bullet with where it is and how to reproduce it;
  `None.` when there are none.

`{work_dir}` is git-ignored scratch space and must never be committed.
Stop once `{work_dir}/verify.md` is written.
