# Manual end-to-end checklist

This checklist is for the template's owner, before cutting the `v1` tag: one full run
against the first adopter, the sample-shop repository.

Times are measured from the moment you add the label. The sample-shop limits are
`first_run_minutes: 120` and `review_round_minutes: 45`.

## Before you start

- [ ] `s0b0lev/agentic-blacksmith` is public (Settings → General → Danger Zone → Change
      visibility), so sample-shop can call its reusable workflows and the droplet can clone
      it.
- [ ] The `feat/v1-template` branch is pushed to `s0b0lev/agentic-blacksmith`.
- [ ] sample-shop has all six secrets and `BLACKSMITH_STATE_BUCKET` set, as in
      [adopting.md](adopting.md) step 7 (`BLACKSMITH_PREVIEW_ZONE` is optional).
- [ ] sample-shop has `.github/workflows/blacksmith.yml` and `blacksmith.yml` from
      `examples/sample-shop/` on its default branch, and a `blacksmith` label.
- [ ] The template ref the workflow pins exists. Before `v1` is tagged, temporarily point
      the two `uses:` lines at `@feat/v1-template` and add `template_ref: feat/v1-template`
      under `with:` in both jobs. Revert this after the tag exists.
- [ ] The DigitalOcean console shows no droplet tagged `blacksmith`.

## The run

- [ ] Create a throwaway issue with a tiny, visible change, for example: "Storefront footer:
      change the copyright line to '© Sample Shop'".
- [ ] Add the `blacksmith` label.
- [ ] Within ~2 minutes: the issue has a "Blacksmith is working on this. Preview will be at
      ..." comment with the preview URL and a link to the workflow run.
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
      DigitalOcean console shows no droplet for this issue.
- [ ] The branch `blacksmith-assets/issue-<n>` no longer exists in sample-shop.
- [ ] Delete the `blacksmith/issue-<n>` branch and close the throwaway issue.

## Self-destruct (second throwaway issue)

- [ ] Create a second throwaway issue and add the `blacksmith` label. Wait for the "working
      on this" comment and for the progress comment's `### Plan` section.
- [ ] SSH into the droplet (see [operations.md](operations.md)) and run
      `sudo systemctl start blacksmith-lifetime.service`.
- [ ] Within ~5 minutes: an "expired" comment on the issue (or the pull request, if one is
      already open), a tear-down run in sample-shop, a "Preview server destroyed." comment,
      and no droplet for this issue in the DigitalOcean console.
- [ ] Delete the `blacksmith/issue-<n>` branch and close the issue.

## Unlabel before a pull request (third throwaway issue)

- [ ] Create a third throwaway issue and add the `blacksmith` label. Wait for the "working on
      this" comment.
- [ ] Before a pull request exists, remove the `blacksmith` label.
- [ ] Within ~5 minutes: a tear-down run in sample-shop, a "Preview server destroyed."
      comment on the issue, and no droplet for this issue in the DigitalOcean console.
- [ ] Delete the `blacksmith/issue-<n>` branch and close the issue.

## After it passes

- [ ] Tag the release in this repository (`git tag v1 && git push origin v1`) and revert the
      temporary `@feat/v1-template` pins in sample-shop.

## If it fails

| Failed at | Look at (see [operations.md](operations.md)) |
|---|---|
| No "working on this" comment | The `blacksmith` workflow run in sample-shop: "Check secrets", "Read config" and "Terraform apply" steps |
| No plan in the progress comment | `/var/log/blacksmith-bootstrap.log` (setup commands), then `journalctl -u blacksmith-run` |
| Preview URL does not answer | `journalctl -u blacksmith-run`, `~blacksmith/repo/.blacksmith/log.md`, `docker compose ls` on the droplet; DNS delegation if you use `BLACKSMITH_PREVIEW_ZONE` |
| No pull request, or a draft `(incomplete)` one | The draft's reason and log, then `.blacksmith/log.md` and `.blacksmith/transcripts/` |
| No reply to the review comment | `journalctl -u blacksmith-poll`, `.blacksmith/state.json` (`stage` must be `done` or `salvaged`) |
| Droplet still exists after closing | The tear-down run in sample-shop; then "Forcing a tear-down" and "Cleaning up leftover droplets" |
| Assets branch still exists | The tear-down run's "Delete assets branch" step; delete the branch by hand |
