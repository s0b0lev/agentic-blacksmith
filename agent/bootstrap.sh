#!/usr/bin/env bash
# shellcheck disable=SC2016  # as_bs scripts are single-quoted on purpose: they expand as blacksmith
set -euo pipefail
# Droplet bootstrap, run once by cloud-init as root: bash /opt/blacksmith/agent/bootstrap.sh
# Reads /etc/blacksmith/env (secrets) and /etc/blacksmith/config (KEY=value, written by
# cloud-init), prepares the machine and starts the runner. Safe to re-run.

AGENT=/opt/blacksmith/agent
ENV_FILE=/etc/blacksmith/env
CONF=/etc/blacksmith/config
HOME_DIR=/home/blacksmith
REPO_ROOT="$HOME_DIR/repo"
PROMPTS_DIR="$AGENT/prompts"
LOG=/var/log/blacksmith-bootstrap.log
CREDENTIAL_HELPER='!f() { echo "username=x-access-token"; echo "password=${BLACKSMITH_GITHUB_TOKEN}"; }; f'
export DEBIAN_FRONTEND=noninteractive

exec > >(tee -a "$LOG") 2>&1
cd /

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "FATAL: $*"; exit 1; }

STEP_N=0
STEP=prelude
step() {
  STEP_N="$1"
  STEP="$2"
  echo "== step $1: $2 =="
}

# Best effort: tell the issue that bootstrap failed. Never changes the exit status.
report_failure() {
  local token repo issue hours body
  token="$(file_get "$ENV_FILE" BLACKSMITH_GITHUB_TOKEN 2>/dev/null || true)"
  repo="$(file_get "$CONF" REPO 2>/dev/null || true)"
  issue="$(file_get "$CONF" ISSUE 2>/dev/null || true)"
  hours="${LIFETIME_HOURS:-168}"
  [ -n "$token" ] && [ -n "$repo" ] && [[ "$issue" =~ ^[0-9]+$ ]] || return 0
  body="Blacksmith bootstrap failed at step $STEP_N ($STEP): see $LOG on the droplet. The server will self-destruct after $hours h.

<!-- blacksmith:bootstrap-failed -->"
  python3 -I -c 'import json, sys; print(json.dumps({"body": sys.argv[1]}))' "$body" \
    | curl -fsS --max-time 30 -o /dev/null -X POST \
        -H "Authorization: Bearer $token" -H "Accept: application/vnd.github+json" \
        --data @- "https://api.github.com/repos/$repo/issues/$issue/comments" \
    || log "could not post the bootstrap-failed comment"
}
on_exit() {
  local status=$?
  trap - ERR
  if [ "$status" -ne 0 ]; then
    log "bootstrap failed (exit $status) at step $STEP_N ($STEP)"
    report_failure || true
  fi
  exit "$status"
}
trap 'log "command failed (exit $?) at line $LINENO"' ERR
trap on_exit EXIT

# KEY=value lookup that strips one layer of quotes, like blacksmith.read_config_file.
file_get() {
  local line value
  line="$(grep -E "^$2=" "$1" | tail -n 1 || true)"
  value="${line#*=}"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value"
}

# Replace KEY's line in the runner config, or append it; values are written unquoted.
set_conf() {
  local tmp
  tmp="$(mktemp)"
  grep -v -E "^$1=" "$CONF" > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  cat "$tmp" > "$CONF"
  rm -f "$tmp"
}

# Run a bash script as blacksmith in a login shell; extra arguments become $1, $2, ...
# runuser keeps the caller's environment apart from HOME/SHELL/USER/LOGNAME.
as_bs() {
  local script="$1"
  shift
  runuser -u blacksmith -- bash -lc "set -eo pipefail; $script" blacksmith-bootstrap "$@" </dev/null
}

# A fresh droplet may still be running apt (unattended-upgrades); wait for the lock.
apt_get() {
  apt-get -o DPkg::Lock::Timeout=600 -y -q "$@"
}

# A blacksmith.yml value by dotted key (see `python3 -m blacksmith config --help`).
bs_config() {
  PYTHONPATH="$AGENT" python3 -P -m blacksmith config --get "$1" --config "$REPO_ROOT/$CONFIG_PATH"
}

[ -f "$ENV_FILE" ] || die "$ENV_FILE is missing"
[ -f "$CONF" ] || die "$CONF is missing"
ISSUE="$(file_get "$CONF" ISSUE)"
REPO="$(file_get "$CONF" REPO)"
BASE_BRANCH="$(file_get "$CONF" BASE_BRANCH)"
CONFIG_PATH="$(file_get "$CONF" CONFIG_PATH)"
PREVIEW_ZONE="$(file_get "$CONF" PREVIEW_ZONE)"
LIFETIME_HOURS="$(file_get "$CONF" LIFETIME_HOURS)"
LIFETIME_HOURS="${LIFETIME_HOURS:-168}"
GITHUB_TOKEN="$(file_get "$ENV_FILE" BLACKSMITH_GITHUB_TOKEN)"
FIGMA_API_KEY="$(file_get "$ENV_FILE" FIGMA_API_KEY)"
for key in ISSUE REPO BASE_BRANCH CONFIG_PATH GITHUB_TOKEN; do
  [ -n "${!key}" ] || die "$key is not set in $CONF / $ENV_FILE"
done
[[ "$ISSUE" =~ ^[0-9]+$ ]] || die "ISSUE must be a number, got '$ISSUE'"
[[ "$LIFETIME_HOURS" =~ ^[0-9]+$ ]] || die "LIFETIME_HOURS must be a number, got '$LIFETIME_HOURS'"
log "bootstrap for $REPO#$ISSUE (base $BASE_BRANCH, config $CONFIG_PATH)"

step 1 "user, secrets and lifetime cap"
id blacksmith >/dev/null 2>&1 || useradd -m -s /bin/bash blacksmith
chown root:blacksmith "$ENV_FILE"
chmod 0640 "$ENV_FILE"
chown root:root "$CONF"
chmod 0644 "$CONF"
chmod -R a+rX /opt/blacksmith
chmod 0755 "$AGENT/bootstrap.sh" "$AGENT/systemd/run.sh" "$AGENT/hooks/pre-push"
# The lifetime cap goes in before anything that can fail (apt, clone, setup), so a failed
# bootstrap still self-destructs. The service needs its WorkingDirectory and the runner
# config keys; later steps fill in the real values. run.sh falls back to a plain curl
# dispatch when the runner itself cannot run yet.
install -d -o blacksmith -g blacksmith "$REPO_ROOT"
set_conf REPO_ROOT "$REPO_ROOT"
set_conf PROMPTS_DIR "$PROMPTS_DIR"
for key in BRANCH PREVIEW_HOST; do
  grep -q -E "^$key=" "$CONF" || set_conf "$key" ""
done
install -m 0644 "$AGENT/systemd/blacksmith-lifetime.service" \
  "$AGENT/systemd/blacksmith-lifetime.timer" /etc/systemd/system/
sed -i "s/^OnBootSec=.*/OnBootSec=${LIFETIME_HOURS}h/" /etc/systemd/system/blacksmith-lifetime.timer
systemctl daemon-reload
systemctl enable --now blacksmith-lifetime.timer
log "lifetime cap: self-destruct ${LIFETIME_HOURS}h after boot"

step 2 "packages, docker, browser, uv and node"
apt_get update
apt_get install ca-certificates curl gnupg git jq build-essential python3 python3-yaml unzip
install -m 0755 -d /etc/apt/keyrings
arch="$(dpkg --print-architecture)"
# shellcheck disable=SC1091
codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $codename stable" \
  > /etc/apt/sources.list.d/docker.list
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$arch signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  > /etc/apt/sources.list.d/github-cli.list
apt_get update
apt_get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin gh
systemctl enable --now docker
usermod -aG docker blacksmith

# chromium-browser on Ubuntu 24.04 is a snap shim that does not run headless as a service
# user, so prefer Google Chrome's .deb (it also registers Google's apt repo).
install_chrome() {
  local deb
  command -v google-chrome-stable >/dev/null && return 0
  [ "$arch" = amd64 ] || return 1
  deb="$(mktemp --suffix=.deb)"
  chmod 0644 "$deb"
  curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o "$deb" \
    && apt_get install "$deb"
  local status=$?
  rm -f "$deb"
  return "$status"
}
if install_chrome; then
  BLACKSMITH_BROWSER=google-chrome-stable
elif apt_get install chromium; then
  BLACKSMITH_BROWSER=chromium
else
  BLACKSMITH_BROWSER=chromium
  log "WARNING: no browser installed; verify and design screenshots will fail"
fi
log "browser: $BLACKSMITH_BROWSER"
set_conf BLACKSMITH_BROWSER "$BLACKSMITH_BROWSER"

# uv, then Node 22 via nvm; ~/.nvm/versions/node/current makes the PATH stable for
# login shells (setup and check commands) and for systemd/run.sh.
as_bs '
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
if [ ! -s "$HOME/.nvm/nvm.sh" ]; then
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | PROFILE=/dev/null bash
fi
. "$HOME/.nvm/nvm.sh" --no-use
nvm install 22
nvm alias default 22
ln -sfn "$(dirname "$(dirname "$(nvm which 22)")")" "$HOME/.nvm/versions/node/current"
line="export PATH=\"\$HOME/.local/bin:\$HOME/.nvm/versions/node/current/bin:\$PATH\" # blacksmith"
grep -qxF "$line" "$HOME/.profile" || printf "%s\n" "$line" >> "$HOME/.profile"
'
as_bs 'echo "uv $(uv --version), node $(node --version), npm $(npm --version)"'

step 3 "claude code"
as_bs 'command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash'
as_bs 'claude --version'
# Fail here, with a comment on the issue, rather than hours later in the first stage.
CLAUDE_TOKEN="$(file_get "$ENV_FILE" CLAUDE_CODE_OAUTH_TOKEN)"
smoke=""
if ! smoke="$(CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_TOKEN" as_bs \
      'timeout 120 claude -p "Reply with the single word ok." --max-turns 1 --output-format json')" \
    || ! printf '%s' "$smoke" \
      | python3 -I -c 'import json, sys; sys.exit(1 if json.load(sys.stdin).get("is_error") else 0)'; then
  die "Claude Code smoke test failed; check the CLAUDE_CODE_OAUTH_TOKEN secret (output: ${smoke:0:300})"
fi
log "claude smoke test: ok"
if ! as_bs 'claude plugin marketplace add anthropics/claude-plugins-official || true
  claude plugin install --scope user superpowers@claude-plugins-official'; then
  log "WARNING: could not install the superpowers plugin; continuing without it"
fi

step 4 "preview host"
if [ -n "$PREVIEW_ZONE" ]; then
  PREVIEW_HOST="pr-$ISSUE.$PREVIEW_ZONE"
else
  ip="$(curl -fsS --retry 5 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address)" \
    || die "cannot read the public IP from the droplet metadata service"
  [ -n "$ip" ] || die "droplet metadata returned an empty public IP"
  PREVIEW_HOST="pr-$ISSUE-${ip//./-}.nip.io"
fi
log "preview host: $PREVIEW_HOST"
set_conf PREVIEW_HOST "$PREVIEW_HOST"
# Containers must not read the droplet's user data (it holds the tokens) from the metadata
# service; the host itself still can (DOCKER-USER only sees forwarded container traffic).
command -v iptables >/dev/null || apt_get install iptables
iptables -C DOCKER-USER -d 169.254.169.254 -j DROP 2>/dev/null \
  || iptables -I DOCKER-USER -d 169.254.169.254 -j DROP

step 5 "git clone, branch and push guard"
as_bs '
git config --global credential.helper "$1"
git config --global user.name "blacksmith[bot]"
git config --global user.email "blacksmith@users.noreply.github.com"
' "$CREDENTIAL_HELPER"
if [ ! -d "$REPO_ROOT/.git" ]; then
  BLACKSMITH_GITHUB_TOKEN="$GITHUB_TOKEN" \
    as_bs 'git clone "https://github.com/$1.git" "$2"' "$REPO" "$REPO_ROOT"
fi
BRANCH_PREFIX="$(bs_config branch_prefix)" || die "cannot read branch_prefix from $CONFIG_PATH"
BRANCH="$BRANCH_PREFIX$ISSUE"
log "branch: $BRANCH"
set_conf BRANCH "$BRANCH"
set_conf REPO_ROOT "$REPO_ROOT"
install -m 0755 -o blacksmith -g blacksmith "$AGENT/hooks/pre-push" "$REPO_ROOT/.git/hooks/pre-push"
# the push guard reads BRANCH and ISSUE from the runner config written above
BLACKSMITH_GITHUB_TOKEN="$GITHUB_TOKEN" as_bs '
cd "$1"
git config push.default nothing
mkdir -p .git/info
for pattern in .blacksmith/ .env; do
  grep -qxF "$pattern" .git/info/exclude 2>/dev/null || echo "$pattern" >> .git/info/exclude
done
git fetch origin
if git rev-parse --verify --quiet "refs/remotes/origin/$2" >/dev/null; then
  git checkout -B "$2" "origin/$2"
else
  git checkout -B "$2" --no-track "origin/$3"
  git push origin "HEAD:refs/heads/$2"
fi
' "$REPO_ROOT" "$BRANCH" "$BASE_BRANCH"

step 6 "skills and mcp"
install -d -o blacksmith -g blacksmith "$HOME_DIR/.claude" "$HOME_DIR/.claude/skills"
for skill in "$AGENT"/skills/*/; do
  name="$(basename "$skill")"
  if [ -e "$REPO_ROOT/.claude/skills/$name" ]; then
    log "skill $name: the repo has its own, skipping"
    continue
  fi
  rm -rf "${HOME_DIR:?}/.claude/skills/$name"
  cp -r "$skill" "$HOME_DIR/.claude/skills/$name"
  log "skill $name: installed"
done
chown -R blacksmith:blacksmith "$HOME_DIR/.claude"
FIGMA_ENABLED="$(bs_config design.figma)" || die "cannot read design.figma from $CONFIG_PATH"
if [ -n "$FIGMA_API_KEY" ] && [ "$FIGMA_ENABLED" = true ] && [ -f "$REPO_ROOT/.mcp.json" ]; then
  python3 -I -c '
import json, os, sys
servers = json.load(open(sys.argv[1])).get("mcpServers", {})
path = sys.argv[2]
data = json.load(open(path)) if os.path.exists(path) else {}
data["enabledMcpjsonServers"] = sorted(set(data.get("enabledMcpjsonServers", [])) | set(servers))
with open(path, "w") as fh:
    fh.write(json.dumps(data, indent=2) + "\n")
print("enabled MCP servers:", ", ".join(data["enabledMcpjsonServers"]) or "none")
' "$REPO_ROOT/.mcp.json" "$HOME_DIR/.claude/settings.json"
  chown blacksmith:blacksmith "$HOME_DIR/.claude/settings.json"
else
  log "MCP servers not enabled (needs FIGMA_API_KEY, design.figma: true and .mcp.json)"
fi

step 7 "setup commands"
SETUP="$(bs_config setup)" || die "cannot read setup from $CONFIG_PATH"
while IFS= read -r cmd; do
  [ -n "$cmd" ] || continue
  log "setup: $cmd"
  if ! as_bs 'cd "$1" && eval "$2"' "$REPO_ROOT" "$cmd"; then
    die "setup command failed: $cmd (the runner was not started)"
  fi
done <<< "$SETUP"
# A setup step such as husky's `npm ci` can point core.hooksPath elsewhere, which would
# bypass the push guard in .git/hooks.
if hooks_path="$(as_bs 'git -C "$1" config --local core.hooksPath' "$REPO_ROOT")"; then
  log "WARNING: setup set core.hooksPath=$hooks_path; unsetting it so the push guard runs"
  as_bs 'git -C "$1" config --local --unset core.hooksPath' "$REPO_ROOT"
fi

step 8 "fetch issue"
BLACKSMITH_GITHUB_TOKEN="$GITHUB_TOKEN" as_bs '
cd "$1"
GH_TOKEN="$BLACKSMITH_GITHUB_TOKEN" PYTHONPATH="$2" python3 -P -m blacksmith fetch-issue
' "$REPO_ROOT" "$AGENT"

step 9 "runner config and systemd units"
set_conf REPO_ROOT "$REPO_ROOT"
set_conf PROMPTS_DIR "$PROMPTS_DIR"
set_conf BRANCH "$BRANCH"
set_conf PREVIEW_HOST "$PREVIEW_HOST"
set_conf BLACKSMITH_BROWSER "$BLACKSMITH_BROWSER"
for unit in "$AGENT"/systemd/*.service "$AGENT"/systemd/*.timer; do
  install -m 0644 "$unit" /etc/systemd/system/
done
sed -i "s/^OnBootSec=.*/OnBootSec=${LIFETIME_HOURS}h/" /etc/systemd/system/blacksmith-lifetime.timer
systemctl daemon-reload
systemctl enable --now blacksmith-lifetime.timer blacksmith-poll.timer
systemctl start --no-block blacksmith-run.service
log "bootstrap done: blacksmith-run.service started for $REPO#$ISSUE on $BRANCH"
