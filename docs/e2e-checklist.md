# Manual end-to-end checklist

This checklist is for the template's owner, before cutting the `v1` tag: one full run
against your adopter repository, called `<owner>/<repo>` below.

Times are measured from the moment you add the label. They assume the limits in the sample
example, `first_run_minutes: 120` and `review_round_minutes: 45`; adjust them if your
`blacksmith.yml` sets other values.

## Before you start

- [ ] `<owner>/<repo>` has all six secrets and `BLACKSMITH_STATE_BUCKET` set, as in
      [adopting.md](adopting.md) step 7 (`BLACKSMITH_PREVIEW_ZONE` is optional).
- [ ] `<owner>/<repo>` has `.github/workflows/blacksmith.yml` and a `blacksmith.yml`
      (start from `examples/sample-shop/`) on its default branch, and a `blacksmith` label.
- [ ] The template ref the workflow pins exists. The `v1` tag is not published yet, so point
      the two `uses:` lines at `@master` and add `template_ref: master` under `with:` in both
      jobs. Switch back to `@v1` after the tag exists.
- [ ] The DigitalOcean console shows no droplet tagged `blacksmith`.

## The run

- [ ] Create a throwaway issue with a tiny, visible change, for example: "Footer: change
      the copyright line to '© <your project name>'".
- [ ] Add the `blacksmith` label.
- [ ] Within ~2 minutes: the issue has a "Blacksmith is working on this. Preview will be at
      ..." comment with the preview URL and a link to the workflow run. With observability
      enabled it also reads "Live dashboard: ...", and the Grafana link opens without a login
      (public repository) showing "Runner events" within ~10 minutes.
- [ ] Within ~10 minutes: the issue's progress comment ("**Blacksmith** is working on this
      issue on branch `blacksmith/issue-<n>`") shows a `### Plan` section.
- [ ] Within ~15–20 minutes: the preview URL answers with a basic-auth prompt, and the
      progress comment shows "Preview is up at ..." with a login that works.
- [ ] Within 120 minutes (the first-run budget): a pull request from `blacksmith/issue-<n>`
      is open, not a draft, with the preview URL, login, a Verification section and
      screenshots that render.
- [ ] Leave one review comment on the pull request asking for a small, checkable change.
- [ ] Within 45 minutes (`review_round_minutes`, plus up to a minute for the poll timer):
      a reply to your comment that ends with a commit sha, a new commit on the branch, and
      a comment reading exactly `Round 1: addressed 1 comment(s) in <sha>; preview ok`.
- [ ] After the round, the preview URL still loads with the same login as before, and shows
      the requested change.
- [ ] Close the pull request without merging.
- [ ] Within ~5 minutes: the pull request has a "Preview server destroyed." comment, and the
      DigitalOcean console shows no droplet for this issue. With observability enabled, the
      Live dashboard link now returns 404.
- [ ] The branch `blacksmith-assets/issue-<n>` no longer exists in `<owner>/<repo>`.
- [ ] Delete the `blacksmith/issue-<n>` branch and close the throwaway issue.

## Self-destruct (second throwaway issue)

- [ ] Create a second throwaway issue and add the `blacksmith` label. Wait for the "working
      on this" comment and for the progress comment's `### Plan` section.
- [ ] SSH into the droplet (see [operations.md](operations.md)) and run
      `sudo systemctl start blacksmith-lifetime.service`.
- [ ] Within ~5 minutes: an "expired" comment on the issue (or the pull request, if one is
      already open), a tear-down run in `<owner>/<repo>`, a "Preview server destroyed." comment,
      and no droplet for this issue in the DigitalOcean console.
- [ ] Delete the `blacksmith/issue-<n>` branch and close the issue.

## Unlabel before a pull request (third throwaway issue)

- [ ] Create a third throwaway issue and add the `blacksmith` label. Wait for the "working on
      this" comment.
- [ ] Before a pull request exists, remove the `blacksmith` label.
- [ ] Within ~5 minutes: a tear-down run in `<owner>/<repo>`, a "Preview server destroyed."
      comment on the issue, and no droplet for this issue in the DigitalOcean console.
- [ ] Delete the `blacksmith/issue-<n>` branch and close the issue.

## After it passes

- [ ] Tag the release in this repository (`git tag v1 && git push origin v1`) and revert the
      temporary `@master` pins in `<owner>/<repo>`.

## If it fails

| Failed at | Look at (see [operations.md](operations.md)) |
|---|---|
| No "working on this" comment | The `blacksmith` workflow run in `<owner>/<repo>`: "Check secrets", "Read config" and "Terraform apply" steps |
| No plan in the progress comment | `/var/log/blacksmith-bootstrap.log` (setup commands), then `journalctl -u blacksmith-run` |
| Preview URL does not answer | `journalctl -u blacksmith-run`, `~blacksmith/repo/.blacksmith/log.md`, `docker compose ls` on the droplet; DNS delegation if you use `BLACKSMITH_PREVIEW_ZONE` |
| No pull request, or a draft `(incomplete)` one | The draft's reason and log, then `.blacksmith/log.md` and `.blacksmith/transcripts/` |
| No reply to the review comment | `journalctl -u blacksmith-poll`, `.blacksmith/state.json` (`stage` must be `done` or `salvaged`) |
| Droplet still exists after closing | The tear-down run in `<owner>/<repo>`; then "Forcing a tear-down" and "Cleaning up leftover droplets" |
| Assets branch still exists | The tear-down run's "Delete assets branch" step; delete the branch by hand |
