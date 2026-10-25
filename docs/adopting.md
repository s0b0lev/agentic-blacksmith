# Adopting agentic blacksmith

This guide is for an engineer who wants to add agentic blacksmith to their own GitHub
repository, from zero to a labelled issue.

You will create four tokens (five with Figma), store them as secrets, copy two files into
your repository, and add a label to an issue. Plan about an hour the first time.

## 1. Prerequisites

Your repository fits if all of these are true:

- It has a Docker Compose file that starts the whole app, and Dockerfiles for the services
  you change (the preview builds those services from the work branch).
- Its setup commands (install dependencies) and check commands (lint, type check, tests) run
  on Ubuntu 24.04 with Docker, Python 3, uv and Node 22 available.
- The app can be previewed over HTTP(S) on one hostname, with paths routed to services (for
  example `/admin*` to one service and `/*` to another).

It does not fit if:

- The app is a mobile app, a desktop app, or a library with nothing to preview.
- The app needs several hostnames, non-HTTP ports, or GPUs to be previewed.
- Setup needs secrets or private networks that you cannot give to a throwaway server.

You also need: a GitHub repository you own (see step 2), a DigitalOcean account, and a Claude
Pro or Max subscription. The template repository (`s0b0lev/agentic-blacksmith`) is public;
your workflow calls its reusable workflows, and the droplet clones it.

## 2. Create the GitHub token

The agent clones, pushes, comments and opens pull requests with one token,
`BLACKSMITH_GITHUB_TOKEN`.

**The repository owner** must create it, as a fine-grained personal access token scoped to
that one repository. A fine-grained token can only target repositories owned by the token's
account (or by an organization that allows such tokens). A collaborator on someone else's
personal repository cannot select that repository when creating a token.

1. Sign in to GitHub as the repository owner.
2. Open Settings → Developer settings → Personal access tokens → Fine-grained tokens →
   Generate new token.
3. Resource owner: the account that owns the repository.
4. Repository access: Only select repositories → pick the repository.
5. Repository permissions, each set to **Read and write**:
   - Contents
   - Pull requests
   - Issues
   (Metadata: Read-only is added automatically.)
6. Set an expiration you are willing to rotate on. Generate the token and copy it.

If the agent will ever change files under `.github/workflows/`, also give the token
**Workflows: Read and write**; GitHub rejects such pushes without it.

**Consequence: the bot and the owner share an identity.** Every comment, pull request and
reply the agent makes appears on GitHub as the token's owner. If the owner also reviews the
pull requests:

- The agent recognises its own comments by a hidden marker (`<!-- blacksmith:... -->`) and
  ignores them in review rounds. Every other comment on the pull request, including the
  owner's, is treated as review feedback.
- The agent asks the issue author to review the pull request, but skips that request when the
  issue author is the token's owner (GitHub does not allow requesting your own review).
- GitHub sees the owner as the pull request's author, so the owner cannot formally approve it.
  Comments and inline review comments still work and still start review rounds.

Commits are authored as `blacksmith[bot] <blacksmith[bot]@users.noreply.github.com>`, so the git
history still shows which commits the agent made.

## 3. DigitalOcean: API token, Spaces bucket and Spaces keys

You need three things from DigitalOcean.

1. **API token** (`DIGITALOCEAN_TOKEN`). In the control panel open API → Tokens → Generate
   New Token. "Full Access" works. If you prefer custom scopes, these are the ones the
   template uses:

   | Resource | Scopes | Why |
   |---|---|---|
   | Droplet | `droplet:create`, `droplet:read`, `droplet:delete` | one droplet per issue; tear-down and the cleanup script delete it |
   | Firewall | `firewall:create`, `firewall:read`, `firewall:update`, `firewall:delete` | one firewall per droplet |
   | SSH key | `ssh_key:create`, `ssh_key:read`, `ssh_key:delete` | a generated key per issue |
   | Tag | `tag:create`, `tag:read` | the `blacksmith`, `repo:<slug>` and `issue:<n>` tags |
   | Action | `action:read` | Terraform waits for droplet and firewall actions to finish |
   | Domain | `domain:read`, `domain:create`, `domain:delete` | only with step 4 (`BLACKSMITH_PREVIEW_ZONE`); DNS records are under the domain scope |

   Spaces is not part of this token: the state bucket uses the separate access key pair from
   item 3. Scope names come from DigitalOcean's custom-scopes UI and were not verified
   against a live token; if the first run fails on a permission error, use a full-access token
   for that run and tighten afterwards.
2. **Spaces bucket** for Terraform state. Either open Spaces Object Storage → Create Bucket,
   pick a region such as `fra1` and a name such as `acme-blacksmith-state`, and keep it
   private, or create it from your machine once you have the key pair from the next item:

   ```sh
   SPACES_ACCESS_KEY_ID=... SPACES_SECRET_ACCESS_KEY=... docs/create-state-bucket.sh acme-blacksmith-state fra1
   ```

   The script needs the `aws` command or `uv` on your machine, creates the bucket private, and
   does nothing if it already exists. The bucket name goes into the repository variable
   `BLACKSMITH_STATE_BUCKET`.
3. **Spaces access key pair** (`SPACES_ACCESS_KEY_ID` and `SPACES_SECRET_ACCESS_KEY`). Open
   Spaces Object Storage → Access Keys → Create Access Key. Give it read, write and delete
   access to the state bucket. The secret is shown once; copy both values.

The reusable workflows assume the bucket is in `fra1`
(`https://fra1.digitaloceanspaces.com`). If your bucket is in another region, add
`spaces_endpoint: https://<region>.digitaloceanspaces.com` under `with:` in both jobs of the
workflow you copy in step 8.

Terraform state lives in that bucket, one workspace per issue (`issue-<n>`). The state
contains the droplet's SSH private key and, as Terraform variables, the GitHub, Claude,
DigitalOcean and Figma tokens. Anyone who can read the bucket can read those. Keep the bucket
private and the Spaces key to yourself.

## 4. Optional: DNS for preview hostnames

Without this step, previews use `pr<n>-<ip-with-dashes>.nip.io` (for example
`pr12-203-0-113-7.nip.io`). That works with no setup. Use your own subdomain if you want
stable, readable hostnames such as `pr-12.preview.example.com`.

1. Pick a subdomain you control, for example `preview.example.com`.
2. At your registrar or DNS host, add NS records that delegate that subdomain to
   DigitalOcean:

   ```text
   preview.example.com.  NS  ns1.digitalocean.com.
   preview.example.com.  NS  ns2.digitalocean.com.
   preview.example.com.  NS  ns3.digitalocean.com.
   ```

3. In DigitalOcean open Networking → Domains and add the domain `preview.example.com`.
4. Set the repository variable `BLACKSMITH_PREVIEW_ZONE` to `preview.example.com`
   (step 7).

Terraform then creates an A record `pr-<n>.preview.example.com` per droplet and deletes it on
tear-down. Caddy on the droplet gets a TLS certificate for the hostname automatically.

## 5. Claude subscription token

The agent runs Claude Code with your Claude subscription.

1. Install Claude Code on your own machine and sign in.
2. Run:

   ```sh
   claude setup-token
   ```

3. Copy the token it prints. It becomes the secret `CLAUDE_CODE_OAUTH_TOKEN`.

The token is tied to your personal subscription and its usage limits. Every droplet's agent
work counts against them, the same as your own Claude Code use. There is no dollar cap. The
only limits are time limits in `blacksmith.yml`: `limits.first_run_minutes` (the first pass),
`limits.review_round_minutes` (each review round) and `limits.lifetime_hours` (how long the
droplet lives).

## 6. Optional: Figma

The design stage compares the change with a Figma design. It runs only when all of these hold:

- `design.figma: true` in `blacksmith.yml`.
- The secret `FIGMA_API_KEY` is set (a Figma personal access token: Figma → Settings →
  Security → Personal access tokens).
- The branch changes at least one file under one of `design.paths`.
- Your repository has a `.mcp.json` at its root that declares a Figma MCP server. Bootstrap
  enables every server in that file only when the first two conditions also hold.

The MCP server reads the key from the environment. For example, with the community
`figma-developer-mcp` server:

```json
{
  "mcpServers": {
    "figma": {
      "command": "npx",
      "args": ["-y", "figma-developer-mcp", "--stdio"],
      "env": { "FIGMA_API_KEY": "${FIGMA_API_KEY}" }
    }
  }
}
```

When the stage is skipped, the pull request's "Design check" section says why.

## 7. Secrets and variables

In your repository open Settings → Secrets and variables → Actions.

On the **Secrets** tab, add:

| Secret | Where it comes from |
|---|---|
| `BLACKSMITH_GITHUB_TOKEN` | Fine-grained token from step 2 |
| `DIGITALOCEAN_TOKEN` | DigitalOcean API token from step 3 |
| `SPACES_ACCESS_KEY_ID` | Spaces access key from step 3 |
| `SPACES_SECRET_ACCESS_KEY` | Spaces secret key from step 3 |
| `CLAUDE_CODE_OAUTH_TOKEN` | Output of `claude setup-token` from step 5 |
| `FIGMA_API_KEY` | Optional; Figma personal access token from step 6 |
| `TELEGRAM_BOT_TOKEN` | Optional; Telegram bot token from step 9 |

On the **Variables** tab, add:

| Variable | Where it comes from |
|---|---|
| `BLACKSMITH_STATE_BUCKET` | Name of the Spaces bucket from step 3 (required) |
| `BLACKSMITH_PREVIEW_ZONE` | Delegated DNS zone from step 4; leave it unset to use nip.io |
| `BLACKSMITH_TELEGRAM_CHAT_ID` | Telegram chat id from step 9; leave it unset to send no notifications |

Finally, create the label: Issues → Labels → New label → name `blacksmith`.

## 8. Copy the two files

### The workflow

Save this as `.github/workflows/blacksmith.yml` in your repository. It is the same file as
[examples/sample-shop/.github/workflows/blacksmith.yml](../examples/sample-shop/.github/workflows/blacksmith.yml).

```yaml
# Copy to .github/workflows/blacksmith.yml in the target repository.
# Needs secrets DIGITALOCEAN_TOKEN, SPACES_ACCESS_KEY_ID, SPACES_SECRET_ACCESS_KEY,
# CLAUDE_CODE_OAUTH_TOKEN, BLACKSMITH_GITHUB_TOKEN (optionally FIGMA_API_KEY,
# TELEGRAM_BOT_TOKEN, and GRAFANA_CLOUD_TOKEN and GRAFANA_API_TOKEN for observability),
# and variables BLACKSMITH_STATE_BUCKET, BLACKSMITH_PREVIEW_ZONE (may be empty for nip.io)
# and BLACKSMITH_TELEGRAM_CHAT_ID (may be empty to skip Telegram).
# The label below must match `label:` in blacksmith.yml. Secrets are mapped explicitly:
# `secrets: inherit` only works within one organization or enterprise.
name: blacksmith

on:
  issues:
    types: [labeled, unlabeled]
  pull_request:
    types: [closed]
  repository_dispatch:
    types: [blacksmith-self-destruct]

jobs:
  spin-up:
    if: github.event_name == 'issues' && github.event.action == 'labeled' && github.event.label.name == 'blacksmith'
    uses: s0b0lev/agentic-blacksmith/.github/workflows/spin-up.yml@v1
    with:
      issue: ${{ github.event.issue.number }}
      state_bucket: ${{ vars.BLACKSMITH_STATE_BUCKET }}
      preview_zone: ${{ vars.BLACKSMITH_PREVIEW_ZONE }}
      telegram_chat_id: ${{ vars.BLACKSMITH_TELEGRAM_CHAT_ID }}
    secrets:
      DIGITALOCEAN_TOKEN: ${{ secrets.DIGITALOCEAN_TOKEN }}
      SPACES_ACCESS_KEY_ID: ${{ secrets.SPACES_ACCESS_KEY_ID }}
      SPACES_SECRET_ACCESS_KEY: ${{ secrets.SPACES_SECRET_ACCESS_KEY }}
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
      BLACKSMITH_GITHUB_TOKEN: ${{ secrets.BLACKSMITH_GITHUB_TOKEN }}
      FIGMA_API_KEY: ${{ secrets.FIGMA_API_KEY }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      GRAFANA_CLOUD_TOKEN: ${{ secrets.GRAFANA_CLOUD_TOKEN }}
      GRAFANA_API_TOKEN: ${{ secrets.GRAFANA_API_TOKEN }}

  resolve:
    if: >-
      (github.event_name == 'issues' && github.event.action == 'unlabeled' && github.event.label.name == 'blacksmith')
      || (github.event_name == 'pull_request' && github.event.action == 'closed' && startsWith(github.event.pull_request.head.ref, 'blacksmith/issue-'))
      || github.event_name == 'repository_dispatch'
    runs-on: ubuntu-latest
    permissions:
      pull-requests: read
    outputs:
      issue: ${{ steps.issue.outputs.issue }}
    steps:
      - name: Resolve issue number
        id: issue
        env:
          EVENT_NAME: ${{ github.event_name }}
          ISSUE_NUMBER: ${{ github.event.issue.number }}
          HEAD_REF: ${{ github.event.pull_request.head.ref }}
          PAYLOAD_ISSUE: ${{ github.event.client_payload.issue }}
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
        run: |
          case "$EVENT_NAME" in
            issues) n="$ISSUE_NUMBER" ;;
            pull_request) n="${HEAD_REF#blacksmith/issue-}" ;;
            repository_dispatch) n="$PAYLOAD_ISSUE" ;;
            *) n="" ;;
          esac
          if ! [[ "$n" =~ ^[1-9][0-9]*$ ]]; then
            echo "::error::cannot resolve an issue number from the $EVENT_NAME event (got '$n')"
            exit 1
          fi
          # Removing the label stops work only before a pull request exists; after that,
          # closing the pull request tears the droplet down.
          if [ "$EVENT_NAME" = issues ]; then
            open="$(gh pr list --repo "$REPO" --head "blacksmith/issue-$n" --state open --json number --jq length)"
            if [ "$open" -ge 1 ]; then
              echo "::notice::issue #$n has an open pull request; close it to tear the droplet down"
              echo "issue=" >> "$GITHUB_OUTPUT"
              exit 0
            fi
          fi
          echo "issue=$n" >> "$GITHUB_OUTPUT"

  tear-down:
    needs: resolve
    if: >-
      ((github.event_name == 'issues' && github.event.action == 'unlabeled' && github.event.label.name == 'blacksmith')
      || (github.event_name == 'pull_request' && github.event.action == 'closed' && startsWith(github.event.pull_request.head.ref, 'blacksmith/issue-'))
      || github.event_name == 'repository_dispatch')
      && needs.resolve.outputs.issue != ''
    uses: s0b0lev/agentic-blacksmith/.github/workflows/tear-down.yml@v1
    with:
      issue: ${{ fromJSON(needs.resolve.outputs.issue) }}
      state_bucket: ${{ vars.BLACKSMITH_STATE_BUCKET }}
      preview_zone: ${{ vars.BLACKSMITH_PREVIEW_ZONE }}
      telegram_chat_id: ${{ vars.BLACKSMITH_TELEGRAM_CHAT_ID }}
    secrets:
      DIGITALOCEAN_TOKEN: ${{ secrets.DIGITALOCEAN_TOKEN }}
      SPACES_ACCESS_KEY_ID: ${{ secrets.SPACES_ACCESS_KEY_ID }}
      SPACES_SECRET_ACCESS_KEY: ${{ secrets.SPACES_SECRET_ACCESS_KEY }}
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
      BLACKSMITH_GITHUB_TOKEN: ${{ secrets.BLACKSMITH_GITHUB_TOKEN }}
      FIGMA_API_KEY: ${{ secrets.FIGMA_API_KEY }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      GRAFANA_CLOUD_TOKEN: ${{ secrets.GRAFANA_CLOUD_TOKEN }}
      GRAFANA_API_TOKEN: ${{ secrets.GRAFANA_API_TOKEN }}
```

Until the `v1` tag is published, pin both `uses:` lines to `@master` and set
`template_ref: master` under `with:` in both jobs; switch to `@v1` once the tag exists.

Things you may change under `with:` in both the `spin-up` and `tear-down` jobs:

- `config_path` (default `blacksmith.yml`): where your config lives.
- `base_branch` (default: the repository's default branch): what the work branch starts from
  and the pull request targets.
- `spaces_endpoint` (default `https://fra1.digitaloceanspaces.com`): see step 3.
- `template_ref` (default `v1`) and `template_repo` (default `s0b0lev/agentic-blacksmith`):
  set both if you run your own fork of this template, and change the `uses:` lines to match.

If you change the label, change it in `blacksmith.yml` and in the three
`github.event.label.name == 'blacksmith'` conditions. If you change `branch_prefix`, change
the two `blacksmith/issue-` strings too.

### The config: `blacksmith.yml`

Save a `blacksmith.yml` at the root of your repository. Start from the annotated example,
[examples/sample-shop/blacksmith.yml](../examples/sample-shop/blacksmith.yml). Unknown keys
are errors. The sections:

**Top level.** `version` must be `1`. `label` (default `blacksmith`) is the trigger label and
must match the workflow. `branch_prefix` (default `blacksmith/issue-`) names the work branch;
issue 12 works on `blacksmith/issue-12`.

`allowed_labelers` (default empty) restricts who can start a run. When it is empty, the user
who added the label must have write, maintain or admin permission on the repository; the
spin-up workflow looks this up with `BLACKSMITH_GITHUB_TOKEN` and fails if the lookup fails.
When it lists GitHub logins (for example `[alice, bob]`), only those users can start a run,
compared case-insensitively, whatever their permission. Anyone else's label is ignored: the
run ends with a notice and no droplet is created.

**`droplet`.** `size` (default `s-4vcpu-8gb`), `region` (default `fra1`) and `image` (default
`ubuntu-24-04-x64`) are DigitalOcean slugs. Bootstrap expects Ubuntu. Pick a size that can
build and run your whole compose stack plus a headless browser.

**`limits`.** `first_run_minutes` (default 120) is the wall-clock budget for the first pass,
split across stages (plan 10%, build 45%, preview fixes 5%, verify 20%, design 10%, pr 5%).
`review_round_minutes` (default 45) caps each review round. `max_turns_per_stage` (default
200) caps agent turns per stage. `lifetime_hours` (default 168) is how long the droplet lives
before it destroys itself.

**`setup`.** Shell commands run once by bootstrap, in the repository root, as the agent user,
in a login shell. Each line is a separate shell, so `cd` does not carry over; write
`cd api && npm ci`. If any command fails, bootstrap stops and the agent never starts.

**`checks_setup`.** Shell commands run before the checks on every verify pass, for example
starting the databases your tests need. A failure counts as a failed required check.

**`checks`.** What the verify stage must run. Each item is a command string, or a mapping with
`run` and `soft`. A failing required check sends the agent back to fix the code (at most two
fix loops). A check with `soft: true` is reported in the pull request but never blocks it.

**`preview`.** How to start the app on the droplet. The preview runs your compose file with a
generated override: every service's published `ports` are removed, and a Caddy container
publishes ports 80 and 443 and routes to your services.

- `compose` (required): path of the compose file to run.
- `project` (required): the compose project name.
- `build`: a map of service name to build context directory. These services are built from
  the work branch instead of using their `image:`.
- `images`: a map of service name to image. Replaces that service's `image:` without touching
  your repository (see section 10).
- `reset`: a map of service name to a list of service keys (such as `command`, `healthcheck`,
  `volumes`) to clear from your compose file, using compose's `!reset` tag. Use it when a
  replacement image needs different settings (see section 10).
- `overrides`: a map of service name to a compose service fragment (any keys a compose service
  accepts). The fragments are written verbatim to a third compose file applied after the
  generated override, so they win. `${VAR}` references are resolved by compose from `.env`.
- `drop`: services to leave out of the preview. A dropped service must not be a `depends_on`
  target of a kept service, or the preview refuses to start.
- `env_example`: an example env file copied to `.env` next to the compose file (compose's
  project directory: `.env`, `env_file:` entries and relative paths in your compose file
  resolve there, as they do when you run `docker compose -f <compose>` yourself). Values that
  are empty or `change-me`, `changeme` or `CHANGE_ME` are replaced with random strings. They
  are generated once per droplet and reused on every later deploy, so database volumes keep
  working; the basic-auth password is reused the same way.
- `env`: values written to `.env` after that, overriding the example. `${PREVIEW_HOST}` is
  replaced with the preview hostname.
- `infra`: services started first (databases, caches, object storage), before `seed`.
- `seed`: one shell command run after `infra` and before the app services, for example
  `docker compose run --rm api python scripts/init_dev_db.py`. It runs in the compose file's
  directory, and `docker compose` inside it targets the preview stack.
- `routes` (required): Caddy routes, matched in order. Each has `path` (a Caddy path matcher
  such as `/admin*`) and `to` (a compose `service:port`). Add `rewrite: "/media/* -> /*"` to
  strip a path prefix before proxying.
- `ready` (default `https://${PREVIEW_HOST}/`): a URL that must answer 2xx or 3xx within 10
  minutes for the preview to count as up.
- `auth` (default `basic`): `basic` puts the whole preview behind HTTP basic auth with user
  `preview` and a random password; `none` turns it off.

**`design`.** `figma` (default `false`) enables the design stage, and `paths` lists path
prefixes (such as `storefront/`) whose changes trigger it. See step 6.

Commit both files to the default branch. Then add the `blacksmith` label to an issue. Within
about two minutes the issue gets a "Blacksmith is working on this" comment with the preview
URL. [operations.md](operations.md) explains what happens next and where to look.

## 9. Optional: Telegram notifications

The agent can post one short message to a Telegram chat for every stage change and
milestone: the plan's first line, "Preview is up at ..." with the login, "Opened <PR URL>",
"Stopped at stage ..." with the reason, and each review round's summary. Every message
starts with the repository and issue and ends with a link to the issue or pull request.
Nothing is sent unless both values below are set.

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and copy its token.
2. Add the bot to the group that should receive the messages.
3. Find the chat id: post a message in the group, then open
   `https://api.telegram.org/bot<token>/getUpdates` in a browser and read `chat.id` (group
   ids are negative).
4. Store the token as the secret `TELEGRAM_BOT_TOKEN` and the chat id as the variable
   `BLACKSMITH_TELEGRAM_CHAT_ID` (step 7). The workflow from step 8 passes both through.

Messages are plain text. The preview login is included, so use a private group. The token
lives on the droplet next to the other secrets; the coding agent's own sessions do not see
it, and a failed message is logged in `.blacksmith/log.md` and never fails a stage.

## 10. Warnings

- The preview is public on the internet. Only basic auth protects it, and the username and
  password are in the pull request body and the issue's progress comment, so anyone who can
  read the repository can open the preview.
- Seed data and integrations are used as-is. Nothing forces sandbox mode. If your
  `env_example`, `env` or seed script points at real payment, email or SMS providers, the
  preview uses them.
- GitHub Free private repositories have no branch protection. The pre-push hook on the
  droplet allows pushes to the work branch and `blacksmith-assets/issue-<n>` only, but it is
  a guard against mistakes, not a security boundary: the agent runs on the same machine as
  the token and could push around the hook. Add branch protection where your plan allows it.
- Starting a droplet costs money and uses your Claude subscription. GitHub only lets users
  with triage or higher add labels; the template additionally requires the labeler to have
  write, maintain or admin permission, or to be listed in `allowed_labelers`. Rejected runs
  still start a short workflow run. To reject them before any runner starts, also add a
  `contains(fromJSON('["alice", "bob"]'), github.actor)` clause to the `spin-up` job's `if:`
  in your workflow (`contains` compares case-insensitively).
- The tokens live in the droplet's user data and in the Terraform state in your Spaces
  bucket. Anyone with root on the droplet or read access to the bucket can read them.
- The agent itself can read the tokens. It runs as the unprivileged `blacksmith` user, but
  that user's environment, the `0640` env file it reads and `/proc` all expose them. The
  runner keeps the GitHub token out of the Claude session's environment, which prevents
  accidents, not a determined agent. Treat everything in the issue and its comments as
  instructions the agent may follow.
- Your preview containers run your code and its dependencies. The droplet's metadata service
  (`169.254.169.254/metadata/v1/user-data`) serves the user data with the tokens; bootstrap
  blocks containers from reaching it with an `iptables` rule in the `DOCKER-USER` chain, but
  anything that gets a shell on the host can still read it.
- Changing any token (rotating a secret) changes the droplet's user data, so the next
  spin-up for an issue that already has a droplet replaces that droplet.

## 11. Images your compose file can no longer pull

Production compose files sometimes reference images that are no longer pullable, for example
a `latest` tag that was removed. The preview would fail on `docker compose up`. Four keys work
around this without changing your repository:

- `preview.images` replaces a service's image when the replacement is a drop-in.
- `preview.reset` clears service keys that do not fit the replacement image.
- `preview.overrides` sets the replacement's own settings.
- `preview.drop` leaves a service out entirely.

The sample example's MinIO recipe uses the last three. `minio/minio:latest` is no longer
pullable, and the Bitnami image that replaces it stores data under `/bitnami/minio/data`, has
no `mc` for the production healthcheck, starts the server itself (so the production
`command: server /data ...` must go), and creates the public bucket from
`MINIO_DEFAULT_BUCKETS`, which replaces the dropped `minio-init` service:

```yaml
preview:
  reset:
    minio: [command, healthcheck, volumes]
  overrides:
    minio:
      image: bitnamilegacy/minio:2025.4.22
      environment:
        MINIO_ROOT_USER: "${MINIO_ACCESS_KEY:-sampleshop}"
        MINIO_ROOT_PASSWORD: "${MINIO_SECRET_KEY}"
        MINIO_DEFAULT_BUCKETS: "product-images:download"
      volumes: ["minio-data:/bitnami/minio/data"]
      healthcheck:
        test: ["CMD-SHELL", "curl -sf http://localhost:9000/minio/health/live || exit 1"]
        interval: 10s
        timeout: 5s
        retries: 10
  drop: [minio-init]
```

Bitnami requires `MINIO_ROOT_USER` of at least 3 characters and `MINIO_ROOT_PASSWORD` of at
least 8; generated passwords are long enough.

This only patches the preview. Fixing the image references in your repository's compose file
is the better long-term move, because your production deploy has the same problem.
