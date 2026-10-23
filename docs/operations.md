# Operating agentic blacksmith

This guide is for whoever runs agentic blacksmith in an adopted repository and needs to see
what it is doing, fix it, or stop it.

Examples below use issue `12` in repository `acme/shop`. Replace both with your own.

## Where to look

Look in this order. Most questions are answered by the first three.

1. **The issue's progress comment.** One comment that starts with "**Blacksmith** is working
   on this issue on branch `blacksmith/issue-12`." It is edited in place as stages finish:
   the plan, "Preview is up at ..." with the login, and "Opened <PR URL>" or "Stopped at
   stage ...". The last line shows the current stage. A separate, earlier comment from the
   workflow ("Blacksmith is working on this. Preview will be at ...") links to the workflow
   run.
2. **The pull request.** The body has the summary, the preview URL and login, the
   verification results and the design check. Each review round adds or updates a comment
   that starts with `Round <n>:`. If the first pass failed, the pull request is a draft titled
   `[blacksmith] <issue title> (incomplete)` with the reason and the last 200 log lines.
3. **The workflow run's summary.** Actions → the `blacksmith` run → Summary. The spin-up job
   prints the preview URL, the droplet IP, the Terraform workspace (`issue-12`) and an SSH
   hint. A failed step's log tells you which secret, variable or config value is wrong.
4. **On the droplet** (see "SSH into the droplet" below):

   | What | Where |
   |---|---|
   | Bootstrap (packages, clone, setup commands) | `/var/log/blacksmith-bootstrap.log` |
   | First pass (plan to pr) | `journalctl -u blacksmith-run` |
   | Review rounds | `journalctl -u blacksmith-poll` |
   | Lifetime expiry | `journalctl -u blacksmith-lifetime` |
   | Runner log, one line per event | `~blacksmith/repo/.blacksmith/log.md` |
   | Current stage, PR number, round | `~blacksmith/repo/.blacksmith/state.json` |
   | Agent transcripts, one file per session | `~blacksmith/repo/.blacksmith/transcripts/` |
   | Stage outputs | `~blacksmith/repo/.blacksmith/plan.md`, `verify.md`, `design.md`, `pr.md` |
   | Preview inputs | `~blacksmith/repo/.blacksmith/Caddyfile`, `compose.override.yml`, `preview.json` |

   Useful commands once you are root on the droplet:

   ```sh
   tail -f /var/log/blacksmith-bootstrap.log
   journalctl -u blacksmith-run -f
   journalctl -u blacksmith-poll --since "1 hour ago"
   tail -n 50 ~blacksmith/repo/.blacksmith/log.md
   docker compose ls
   ```

If bootstrap failed, `/var/log/blacksmith-bootstrap.log` ends with a `FATAL:` line or
`bootstrap failed (exit N) at line L`. The most common cause is a failing `setup` command.

## SSH into the droplet

The droplet's SSH key exists only in the Terraform state. To read it, initialise Terraform
against the same backend the workflow uses.

1. Install Terraform 1.9 or newer, and clone the template at the ref your workflow pins
   (`v1`, or `feat/v1-template` while running the end-to-end checklist before `v1` exists):

   ```sh
   git clone --branch <the ref your workflow pins, e.g. v1> https://github.com/s0b0lev/agentic-blacksmith.git
   cd agentic-blacksmith/terraform
   ```

2. Export the Spaces keys and the values that locate the state. `SLUG` is your repository as
   `owner-name`, lowercased (`acme/shop` becomes `acme-shop`):

   ```sh
   export AWS_ACCESS_KEY_ID='<SPACES_ACCESS_KEY_ID>'
   export AWS_SECRET_ACCESS_KEY='<SPACES_SECRET_ACCESS_KEY>'
   STATE_BUCKET='<BLACKSMITH_STATE_BUCKET>'
   SPACES_ENDPOINT='https://fra1.digitaloceanspaces.com'
   SLUG='acme-shop'
   ```

3. Initialise the backend exactly as the workflow does:

   ```sh
   terraform init -input=false \
     -backend-config="bucket=$STATE_BUCKET" \
     -backend-config="key=blacksmith/$SLUG/terraform.tfstate" \
     -backend-config="region=us-east-1" \
     -backend-config="endpoints={s3=\"$SPACES_ENDPOINT\"}" \
     -backend-config="skip_credentials_validation=true" \
     -backend-config="skip_region_validation=true" \
     -backend-config="skip_requesting_account_id=true" \
     -backend-config="skip_metadata_api_check=true" \
     -backend-config="skip_s3_checksum=true" \
     -backend-config="use_path_style=false"
   ```

4. Select the issue's workspace and connect:

   ```sh
   terraform workspace select issue-12
   terraform output -raw ip
   terraform output -raw ssh_private_key > key && chmod 600 key && ssh -i key root@<ip>
   ```

Delete `key` when you are done. It is root access to a machine that holds your tokens.

## Running a step by hand

The systemd units run `/opt/blacksmith/agent/systemd/run.sh <command>` as the `blacksmith`
user, with `/etc/blacksmith/env` and `/etc/blacksmith/config` loaded. To run a command the
same way from a root shell, use `systemd-run` with the same settings:

```sh
systemd-run --quiet --pipe --wait --collect \
  --uid=blacksmith --gid=blacksmith \
  --working-directory=/home/blacksmith/repo \
  -p EnvironmentFile=/etc/blacksmith/env \
  -p EnvironmentFile=/etc/blacksmith/config \
  -E PYTHONPATH=/opt/blacksmith/agent -E HOME=/home/blacksmith \
  /opt/blacksmith/agent/systemd/run.sh status
```

Replace the last word with the command you need:

- `run.sh status` prints `.blacksmith/state.json` (or `no state`).
- `run.sh preview` redeploys the preview stack from the current checkout and prints
  `preview.json`. Do not run it while the first pass is running
  (`systemctl is-active blacksmith-run` prints `active`).
- `run.sh poll` runs one review round now if the pull request has new comments, and prints
  `round: yes` or `round: no`. The timer does this every minute anyway.

To start a whole unit instead, and read its output in the journal:

```sh
systemctl start blacksmith-poll.service
journalctl -u blacksmith-poll -n 50
```

## Forcing a tear-down

Any of these runs the tear-down workflow, which destroys the droplet, deletes the Terraform
workspace and the `blacksmith-assets/issue-12` branch, and posts "Preview server destroyed."
on the pull request (or on the issue when there is no pull request).

- Close the pull request (merged or not).
- Remove the `blacksmith` label from the issue. This is the way to stop work before a pull
  request exists; it also works after.
- Send the `blacksmith-self-destruct` event yourself:

  ```sh
  echo '{"event_type":"blacksmith-self-destruct","client_payload":{"issue":12}}' \
    | gh api repos/acme/shop/dispatches --input -
  ```

The droplet sends the same event when `limits.lifetime_hours` is over, after posting an
"expired" comment.

## Cleaning up leftover droplets

[cleanup-droplets.sh](cleanup-droplets.sh) is the backstop for droplets that a failed or
cancelled tear-down left behind. It lists droplets tagged `blacksmith` older than a number of
hours (default 168) and deletes them. It needs `curl`, `jq` and a DigitalOcean API token.

The `blacksmith` tag spans every repository that shares the DigitalOcean account. Without
`--repo`, `MAX_AGE_HOURS` must be at least the largest `limits.lifetime_hours` of any of those
repositories, or the script deletes a healthy droplet of a repository with a longer lifetime.
`--repo OWNER/NAME` keeps only droplets tagged `repo:<slug>` of that repository.

```sh
export DIGITALOCEAN_TOKEN='<DIGITALOCEAN_TOKEN>'
docs/cleanup-droplets.sh --dry-run                    # list all droplets older than 168 hours
docs/cleanup-droplets.sh --dry-run --repo acme/shop   # only acme/shop's droplets
docs/cleanup-droplets.sh --repo acme/shop 24          # delete acme/shop's droplets older than 24 hours
```

The script deletes droplets only. The droplet name is
`blacksmith-<owner-name-slug>-issue-<n>`, where the slug is `owner/name` lowercased with every
run of characters outside `a-z`, `0-9` and `-` replaced by `-`; for example
`acme/sample-shop` issue 42 is `blacksmith-acme-sample-shop-issue-42`. Its
firewall, SSH key, DNS record and Terraform workspace stay behind. To remove those, send the
`blacksmith-self-destruct` event for that issue (above): `terraform destroy` notices the
droplet is gone and removes the rest.

## Caveats

- **Do not re-run bootstrap on a live droplet.** It starts the first pass again from the
  plan stage and resets the local work branch to the one on GitHub, dropping unpushed
  commits.
- **A rapid label, unlabel, relabel can cancel a queued tear-down.** Spin-up and tear-down
  for one issue share a concurrency group, and GitHub keeps only one queued job per group. A
  running tear-down is never cancelled. If a droplet survives an unlabel, close the pull
  request or send the dispatch event.
- **The lifetime timer counts from boot.** Rebooting the droplet restarts the clock.
- **Review rounds consume comments even when the agent session fails.** The round comment
  then says `agent session did not finish cleanly` or `Round <n> failed`. Those comments are
  not retried; post a new comment to try again.
- **Every comment without the hidden marker counts as review feedback,** including comments
  from other bots (CI, deploy previews). Each one can start a review round.
- **Assets branches** (`blacksmith-assets/issue-<n>`, which hold the PR screenshots) are
  deleted by tear-down. Screenshot links in a closed pull request stop working after that.
- **Anything left behind** after a failed tear-down is covered by the cleanup script above.
