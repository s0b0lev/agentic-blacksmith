#!/usr/bin/env bash
# Create the private DigitalOcean Spaces bucket that holds Terraform state for the template.
# DigitalOcean has no doctl command for buckets; Spaces speaks the S3 API, so this uses the
# AWS CLI with the Spaces access key pair. It is idempotent: an existing bucket is left as is.
#
# Usage: SPACES_ACCESS_KEY_ID=... SPACES_SECRET_ACCESS_KEY=... docs/create-state-bucket.sh [--dry-run] NAME [REGION]
#
# NAME    bucket name, 3-63 characters of lowercase letters, digits, "." and "-"
# REGION  Spaces region (default fra1); the reusable workflows assume fra1, any other region
#         needs spaces_endpoint under with: in the adopter workflow
#
# Needs the "aws" command on PATH, or uv (then "uvx --from awscli aws" is used).
set -euo pipefail

usage() {
  cat >&2 <<USAGE
usage: SPACES_ACCESS_KEY_ID=... SPACES_SECRET_ACCESS_KEY=... $0 [--dry-run] NAME [REGION]
  --dry-run  print the commands, create nothing
  NAME       bucket name (3-63 chars: a-z, 0-9, ".", "-")
  REGION     Spaces region, default fra1
USAGE
}

dry_run=false
positional=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --dry-run) dry_run=true ;;
    --*) echo "error: unknown option $1" >&2; usage; exit 2 ;;
    *) positional+=("$1") ;;
  esac
  shift
done
if [ "${#positional[@]}" -lt 1 ] || [ "${#positional[@]}" -gt 2 ]; then
  usage
  exit 2
fi
name="${positional[0]}"
region="${positional[1]:-fra1}"

if ! printf '%s' "$name" | grep -Eq '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$'; then
  echo "error: bucket name '$name' must be 3-63 characters of a-z, 0-9, '.' and '-'" >&2
  exit 2
fi
if ! printf '%s' "$region" | grep -Eq '^[a-z]{3}[0-9]$'; then
  echo "error: region '$region' does not look like a Spaces region such as fra1" >&2
  exit 2
fi

missing=""
[ -n "${SPACES_ACCESS_KEY_ID:-}" ] || missing="$missing SPACES_ACCESS_KEY_ID"
[ -n "${SPACES_SECRET_ACCESS_KEY:-}" ] || missing="$missing SPACES_SECRET_ACCESS_KEY"
if [ -n "$missing" ]; then
  echo "error: set these environment variables first:$missing" >&2
  exit 2
fi

if command -v aws >/dev/null 2>&1; then
  aws_cmd=(aws)
elif command -v uvx >/dev/null 2>&1; then
  aws_cmd=(uvx --from awscli aws)
else
  echo "error: need the aws command or uv (for 'uvx --from awscli aws') on PATH" >&2
  exit 2
fi

endpoint="https://${region}.digitaloceanspaces.com"
export AWS_ACCESS_KEY_ID="$SPACES_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$SPACES_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION=us-east-1   # Spaces ignores the region; the CLI needs one set
unset AWS_SESSION_TOKEN AWS_PROFILE

s3() {
  "${aws_cmd[@]}" --endpoint-url "$endpoint" s3api "$@"
}

if [ "$dry_run" = true ]; then
  echo "would run: ${aws_cmd[*]} --endpoint-url $endpoint s3api head-bucket --bucket $name"
  echo "would run: ${aws_cmd[*]} --endpoint-url $endpoint s3api create-bucket --bucket $name --acl private"
  exit 0
fi

if s3 head-bucket --bucket "$name" >/dev/null 2>&1; then
  echo "bucket '$name' already exists at $endpoint; nothing to do"
else
  s3 create-bucket --bucket "$name" --acl private >/dev/null
  echo "created private bucket '$name' at $endpoint"
fi

echo
echo "Set these in the adopter repository:"
echo "  variable BLACKSMITH_STATE_BUCKET = $name"
if [ "$region" != "fra1" ]; then
  echo "  with: spaces_endpoint: $endpoint   (both jobs of .github/workflows/blacksmith.yml)"
fi
