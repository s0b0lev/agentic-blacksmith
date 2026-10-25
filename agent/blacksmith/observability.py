"""Render the droplet's Grafana Alloy config from the runner config and env files."""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from blacksmith.scrub import MIN_SECRET_LENGTH

MAX_LINE_BYTES = 250_000

KEPT_SERIES = (
    "node_cpu_seconds_total", "node_memory_MemTotal_bytes", "node_memory_MemAvailable_bytes",
    "node_filesystem_size_bytes", "node_filesystem_avail_bytes",
    "node_network_receive_bytes_total", "node_network_transmit_bytes_total", "node_load1",
    "container_cpu_usage_seconds_total", "container_memory_working_set_bytes",
    "container_last_seen", "up",
)

JOURNAL_UNITS = "(blacksmith-.*|alloy|docker|containerd|cloud-init.*|cloud-final|cloud-config)\\.service"


@dataclass(frozen=True)
class AlloySettings:
    repo: str
    issue: str
    branch: str
    loki_url: str
    loki_user: str
    prometheus_url: str          # "" disables metrics
    prometheus_user: str
    agent_stream: bool
    secrets: tuple[str, ...]     # raw values to redact


def _alloy_string(value: str) -> str:
    """An Alloy string literal (Go-style escapes, which JSON's are a subset of)."""
    return json.dumps(value)


def redaction_pattern(values: Iterable[str]) -> str:
    """Regex alternation of the secret values, already escaped for an Alloy string literal."""
    kept = sorted({v for v in values if len(v) >= MIN_SECRET_LENGTH}, key=len, reverse=True)
    pattern = "|".join(re.escape(v) for v in kept)
    return pattern.replace("\\", "\\\\").replace('"', '\\"')


def settings_from_files(runner_conf: Mapping[str, str], env: Mapping[str, str]) -> AlloySettings | None:
    """Settings for this droplet, or None when observability is off or has no token."""
    if runner_conf.get("OBSERVABILITY_ENABLED") != "true" or not env.get("GRAFANA_CLOUD_TOKEN"):
        return None
    return AlloySettings(
        repo=runner_conf.get("REPO", ""),
        issue=runner_conf.get("ISSUE", ""),
        branch=runner_conf.get("BRANCH", ""),
        loki_url=runner_conf.get("LOKI_URL", ""),
        loki_user=runner_conf.get("LOKI_USER", ""),
        prometheus_url=runner_conf.get("PROMETHEUS_URL", ""),
        prometheus_user=runner_conf.get("PROMETHEUS_USER", ""),
        agent_stream=runner_conf.get("AGENT_STREAM") == "true",
        secrets=tuple(v for v in env.values() if len(v) >= MIN_SECRET_LENGTH),
    )


def _common_labels(s: AlloySettings) -> str:
    return (f"{{ repo = {_alloy_string(s.repo)}, issue = {_alloy_string(s.issue)}, "
            f"branch = {_alloy_string(s.branch)} }}")


def _logs(s: AlloySettings) -> str:
    targets = [
        '    { __path__ = "/home/blacksmith/repo/.blacksmith/log.md", job = "runner" },',
        '    { __path__ = "/var/log/blacksmith-bootstrap.log", job = "bootstrap" },',
    ]
    if s.agent_stream:
        targets.append('    { __path__ = "/home/blacksmith/.claude/projects/*/*.jsonl", job = "agent" },')
    truncate = (f"{{{{ if gt (len .Entry) {MAX_LINE_BYTES} }}}}"
                f"{{{{ slice .Entry 0 {MAX_LINE_BYTES} }}}} …[truncated]"
                "{{ else }}{{ .Entry }}{{ end }}")
    pattern = redaction_pattern(s.secrets)
    replace = (f'  stage.replace {{\n    expression = "({pattern})"\n'
               '    replace    = "<redacted>"\n  }\n') if pattern else ""
    return f"""\
// ---- runner event log, bootstrap log, Claude Code session stream -------------------
local.file_match "files" {{
  path_targets = [
{chr(10).join(targets)}
  ]
  sync_period = "5s"
}}
loki.source.file "files" {{
  targets    = local.file_match.files.targets
  forward_to = [loki.process.scrub.receiver]
}}

// ---- systemd journal -----------------------------------------------------------------
loki.relabel "journal" {{
  forward_to = []
  rule {{
    source_labels = ["__journal__systemd_unit"]
    regex         = {_alloy_string(JOURNAL_UNITS)}
    action        = "keep"
  }}
  rule {{
    source_labels = ["__journal__systemd_unit"]
    target_label  = "unit"
  }}
  rule {{
    target_label = "job"
    replacement = "journal"
  }}
}}
loki.source.journal "journal" {{
  max_age       = "24h"
  relabel_rules = loki.relabel.journal.rules
  forward_to    = [loki.process.scrub.receiver]
}}

// ---- docker container logs -----------------------------------------------------------
discovery.docker "local" {{
  host = "unix:///var/run/docker.sock"
}}
discovery.relabel "docker" {{
  targets = discovery.docker.local.targets
  rule {{
    source_labels = ["__meta_docker_container_name"]
    regex         = "/(.*)"
    target_label  = "container"
  }}
  rule {{
    source_labels = ["__meta_docker_container_label_com_docker_compose_service"]
    target_label  = "service"
  }}
  rule {{
    source_labels = ["__meta_docker_container_label_com_docker_compose_project"]
    target_label  = "project"
  }}
  rule {{
    target_label = "job"
    replacement = "docker"
  }}
}}
loki.source.docker "docker" {{
  host       = "unix:///var/run/docker.sock"
  targets    = discovery.relabel.docker.output
  forward_to = [loki.process.scrub.receiver]
}}

// ---- truncate, redact secrets, add common labels, ship -------------------------------
loki.process "scrub" {{
  stage.template {{
    source   = "truncated"
    template = {_alloy_string(truncate)}
  }}
  stage.output {{
    source = "truncated"
  }}
{replace}  stage.static_labels {{
    values = {_common_labels(s)}
  }}
  forward_to = [loki.write.cloud.receiver]
}}
loki.write "cloud" {{
  endpoint {{
    url = sys.env("GC_LOKI_URL")
    basic_auth {{
      username = sys.env("GC_LOKI_USER")
      password = sys.env("GC_TOKEN")
    }}
  }}
}}
"""


def _metrics(s: AlloySettings) -> str:
    return f"""
// ---- metrics: node and containers, limited to a keep-list ----------------------------
prometheus.exporter.unix "node" {{ }}
prometheus.exporter.cadvisor "docker" {{
  docker_host = "unix:///var/run/docker.sock"
}}
prometheus.scrape "node" {{
  targets         = prometheus.exporter.unix.node.targets
  forward_to      = [prometheus.relabel.keep.receiver]
  scrape_interval = "15s"
}}
prometheus.scrape "cadvisor" {{
  targets         = prometheus.exporter.cadvisor.docker.targets
  forward_to      = [prometheus.relabel.keep.receiver]
  scrape_interval = "15s"
}}
prometheus.relabel "keep" {{
  forward_to = [prometheus.remote_write.cloud.receiver]
  rule {{
    source_labels = ["__name__"]
    regex         = {_alloy_string("|".join(KEPT_SERIES))}
    action        = "keep"
  }}
}}
prometheus.remote_write "cloud" {{
  external_labels = {_common_labels(s)}
  endpoint {{
    url = sys.env("GC_PROM_URL")
    basic_auth {{
      username = sys.env("GC_PROM_USER")
      password = sys.env("GC_TOKEN")
    }}
  }}
}}
"""


def render_alloy_config(s: AlloySettings) -> str:
    """The full config.alloy text; credentials are read by Alloy from its environment."""
    header = "// Rendered by `python3 -m blacksmith alloy-config`. Credentials come from sys.env.\n\n"
    return header + _logs(s) + (_metrics(s) if s.prometheus_url else "")
