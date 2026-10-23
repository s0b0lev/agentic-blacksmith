#!/usr/bin/env bash
# Delete blacksmith droplets older than MAX_AGE_HOURS (default 168), as a backstop for
# droplets that a failed tear-down left behind. Every droplet the template creates carries
# the DigitalOcean tag "blacksmith".
#
# Usage: DIGITALOCEAN_TOKEN=... docs/cleanup-droplets.sh [--dry-run] [MAX_AGE_HOURS]
#
# This deletes the droplet only. Its firewall, SSH key, DNS record and Terraform workspace
# stay behind; see docs/operations.md for removing those.
set -euo pipefail

API=https://api.digitalocean.com/v2

usage() {
  echo "usage: DIGITALOCEAN_TOKEN=... $0 [--dry-run] [MAX_AGE_HOURS]" >&2
}

dry_run=false
max_age=168
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=true ;;
    -h|--help) usage; exit 0 ;;
    *)
      if [[ "$arg" =~ ^[0-9]+$ ]]; then
        max_age="$arg"
      else
        usage
        exit 2
      fi
      ;;
  esac
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
candidates="$(jq -r --argjson max "$max_age" '
  .droplets[]
  | ((now - (.created_at | fromdateiso8601)) / 3600 | floor) as $age
  | select($age >= $max)
  | [.id, .name, $age, (.tags | join(","))] | @tsv
' <<<"$droplets")"

if [ -z "$candidates" ]; then
  echo "no blacksmith droplets older than ${max_age}h"
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
