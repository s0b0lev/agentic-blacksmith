Use the blacksmith-design-check skill.

You are comparing the implementation of issue #{issue} (branch `{branch}`) with its Figma design.
You run unattended: never ask a human a question and never wait for an answer.

Tokens and passwords exist only as environment variables. Never print, commit, or paste them anywhere.

## Inputs

- `{work_dir}/issue.md` and `{work_dir}/plan.md`: find the Figma links or frame names they cite.
- `{work_dir}/shots/*.png`: screenshots of the running preview ({preview_url}).
- `{work_dir}/verify.md`: what the screenshots show.
- Figma: use the Figma MCP server declared in the repository's `.mcp.json`. Pull only the
  frames the issue or plan reference (layout, spacing, colours, typography, copy).

If no Figma MCP tool is available in this session, write `{work_dir}/design.md` containing
exactly the three headings below with `Figma MCP not available in this session.` under
`## Compared` and nothing under the other two, and stop.

## Do

- Compare each referenced frame with the matching screenshot and the code.
- Fix clear mismatches only (wrong colour token, spacing, font size, missing element, copy).
  Leave matters of taste and anything the issue does not ask for.
- To screenshot a page the shots do not cover: `"$BLACKSMITH_BROWSER" --headless=new --no-sandbox
  --disable-gpu --window-size=1366,900 --screenshot=<file> 'https://user:password@host/path'`
  (basic auth: {preview_auth}). The preview is not redeployed after your fixes, so it
  shows the code before them; do not retake screenshots to confirm a fix.
- Commit fixes with `git commit`. Never push; the runner pushes.

## Write

Write `{work_dir}/design.md` with these sections:

- `## Compared`: each frame (name or node id) and the screenshot it was compared with.
- `## Fixed`: each mismatch fixed, with the commit sha.
- `## Remaining`: each mismatch left, and why.

`{work_dir}` is git-ignored scratch space and must never be committed.
