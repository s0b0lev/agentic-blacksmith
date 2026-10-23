#!/usr/bin/env bash
set -euo pipefail
# Entry point for the blacksmith systemd units: python3 -m blacksmith <command>.
# systemd cannot alias one variable to another, so GH_TOKEN is derived here.
# bootstrap points ~/.nvm/versions/node/current at the installed Node, so PATH needs no
# nvm.sh (which is not safe under set -u).

export GH_TOKEN="${BLACKSMITH_GITHUB_TOKEN:-}"
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/current/bin:$PATH"
exec python3 -P -m blacksmith "$@"
