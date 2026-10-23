#!/usr/bin/env bash
set -euo pipefail
# Entry point for the blacksmith systemd units: python3 -m blacksmith <command>.
# systemd cannot alias one variable to another, so GH_TOKEN is derived here.
# bootstrap points ~/.nvm/versions/node/current at the installed Node, so PATH needs no
# nvm.sh (which is not safe under set -u).

export GH_TOKEN="${BLACKSMITH_GITHUB_TOKEN:-}"
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/current/bin:$PATH"
if [ "${1:-}" != self-destruct ]; then
  exec python3 -P -m blacksmith "$@"
fi
# The droplet must go even when the runner cannot run (for example bootstrap failed before
# gh or the repository were in place): fall back to dispatching the tear-down with curl.
if python3 -P -m blacksmith self-destruct; then
  exit 0
fi
echo "runner self-destruct failed; dispatching blacksmith-self-destruct with curl" >&2
exec curl -fsS --max-time 30 -X POST \
  -H "Authorization: Bearer ${BLACKSMITH_GITHUB_TOKEN:-}" -H "Accept: application/vnd.github+json" \
  --data "{\"event_type\":\"blacksmith-self-destruct\",\"client_payload\":{\"issue\":${ISSUE:-0}}}" \
  "https://api.github.com/repos/${REPO:-}/dispatches"
