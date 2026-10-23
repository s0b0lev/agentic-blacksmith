#!/usr/bin/env bash
# Delete blacksmith droplets older than MAX_AGE_HOURS (default 168), as a backstop for
# droplets that a failed tear-down left behind. Every droplet the template creates carries
# the DigitalOcean tags "blacksmith" and "repo:<slug>".
#
# Usage: DIGITALOCEAN_TOKEN=... docs/cleanup-droplets.sh [--dry-run] [--repo OWNER/NAME] [MAX_AGE_HOURS]
#
# The "blacksmith" tag spans every repository that shares this DigitalOcean account. Without
# --repo, MAX_AGE_HOURS must be at least the largest limits.lifetime_hours of any of them, or a
# healthy droplet of a repository with a longer lifetime is deleted. --repo OWNER/NAME keeps
# only droplets tagged repo:<slug>, where the slug is OWNER/NAME lowercased with every run of
# characters outside [a-z0-9-] replaced by "-" and leading/trailing "-" trimmed (as in
# terraform/main.tf).
#
# This deletes the droplet only. Its firewall, SSH key, DNS record and Terraform workspace
# stay behind; see docs/operations.md for removing those.
set -euo pipefail

API=https://api.digitalocean.com/v2

usage() {
  cat >&2 <<USAGE
usage: DIGITALOCEAN_TOKEN=... $0 [--dry-run] [--repo OWNER/NAME] [MAX_AGE_HOURS]
  --dry-run          list the droplets that would be deleted, delete nothing
  --repo OWNER/NAME  only droplets of this repository (tag repo:<slug>)
  MAX_AGE_HOURS      minimum age to delete (default 168); without --repo it must be at least
                     the largest limits.lifetime_hours of every repository on this account
USAGE
}

# The DigitalOcean-safe slug terraform/main.tf uses in the repo:<slug> tag.
repo_slug() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//'
}

dry_run=false
max_age=168
repo_tag=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=true ;;
    --repo)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then
        echo "error: --repo needs OWNER/NAME" >&2
        usage
        exit 2
      fi
      repo_tag="repo:$(repo_slug "$2")"
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        max_age="$1"
      else
        echo "error: unknown argument: $1" >&2
        usage
        exit 2
      fi
      ;;
  esac
  shift
done

if [ -z "${DIGITALOCEAN_TOKEN:-}" ]; then
  echo "error: DIGITALOCEAN_TOKEN is not set (a DigitalOcean API token with droplet access)" >&2
  usage
  exit 2
fi
for tool in curl jq; do
  command -v "$tool" >/dev/null || { echo "error: $tool is required" >&2; exit 2; }
done

# The token goes to curl through a header file on stdin, never on the command line.
api() {
  local method="$1" path="$2"
  printf 'Authorization: Bearer %s\n' "$DIGITALOCEAN_TOKEN" \
    | curl -fsS -X "$method" -H @- -H "Content-Type: application/json" "$API$path"
}

droplets="$(api GET "/droplets?tag_name=blacksmith&per_page=200")"
candidates="$(jq -r --argjson max "$max_age" --arg repo "$repo_tag" '
  .droplets[]
  | select($repo == "" or (.tags | index($repo)) != null)
  | ((now - (.created_at | fromdateiso8601)) / 3600 | floor) as $age
  | select($age >= $max)
  | [.id, .name, $age, (.tags | join(","))] | @tsv
' <<<"$droplets")"

if [ -z "$candidates" ]; then
  echo "no blacksmith droplets${repo_tag:+ tagged $repo_tag} older than ${max_age}h"
  exit 0
fi

while IFS=$'\t' read -r id name age tags; do
  if [ "$dry_run" = true ]; then
    echo "would delete: $name (id $id, ${age}h old, tags $tags)"
  else
    echo "deleting: $name (id $id, ${age}h old, tags $tags)"
    api DELETE "/droplets/$id" >/dev/null
  fi
done <<<"$candidates"
exit 0
