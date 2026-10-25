# Agentic blacksmith

This page is for engineers deciding whether to use this template in their own repository.

Agentic blacksmith turns a GitHub issue into a pull request. You add a label to an issue. A
GitHub Actions workflow creates a DigitalOcean droplet for that issue. On the droplet, Claude
Code plans the change, builds it, starts a live preview of your app with Docker Compose,
runs your checks, and opens a pull request with screenshots and a preview link behind basic
auth.

Review comments on that pull request start fix rounds: the agent replies to each comment and
pushes a commit. Closing the pull request destroys the droplet. Nothing runs on your laptop,
and nothing stays running after the work is done.

## The flow

```text
label "blacksmith" on an issue
  -> adopter workflow (.github/workflows/blacksmith.yml in your repo)
  -> reusable spin-up workflow (this repo)
  -> Grafana dashboard per issue (optional): logs and metrics from boot
  -> terraform apply: one droplet, firewall, SSH key, optional DNS record
  -> droplet bootstrap: Docker, Node, uv, Claude Code, clone, your setup commands
  -> stages: plan -> build -> preview -> verify -> design -> pr
  -> pull request with preview URL, login and screenshots
  -> review rounds: new PR comments -> agent replies and pushes -> preview redeploys
  -> PR closed (or label removed, or lifetime over) -> tear-down workflow -> terraform destroy
```

## Adopt it in three steps

1. Create a GitHub token, a DigitalOcean token and Spaces bucket, and a Claude token, and
   store them as repository secrets.
2. Copy two files into your repository: `.github/workflows/blacksmith.yml` and
   `blacksmith.yml`.
3. Add the `blacksmith` label to an issue.

The full guide, with every name to type, is [docs/adopting.md](docs/adopting.md). When
something goes wrong, read [docs/operations.md](docs/operations.md).

## Status

v1 — backend and web frontend projects that run with Docker Compose; mobile is out of scope.

The repository is public and the code is complete and reviewed, but the `v1` tag is not
published yet: it follows the first full run of [docs/e2e-checklist.md](docs/e2e-checklist.md).
Until then, adopters pin `@master` as described in the adoption guide.

## Repository layout

- `.github/workflows/` — the reusable `spin-up.yml` and `tear-down.yml` workflows adopters call.
- `agent/` — what runs on the droplet: the Python runner (`blacksmith/`), prompts, skills,
  `bootstrap.sh`, systemd units, the pre-push guard, and tests. `observability.py` renders
  the Alloy config and `dashboard.py` creates and deletes the per-issue Grafana dashboard.
- `terraform/` — one droplet per issue, with its firewall, SSH key and optional DNS record.
- `examples/` — a sample adopter's workflow and `blacksmith.yml` (`sample-shop`).
- `docs/` — adoption guide, operations guide, end-to-end checklist, and scripts to create the
  state bucket and clean up leftover droplets.
