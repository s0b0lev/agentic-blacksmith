import shutil
import subprocess

import pytest

from blacksmith.observability import (
    MAX_LINE_BYTES, AlloySettings, redaction_pattern, render_alloy_config, settings_from_files,
)


def settings(**over) -> AlloySettings:
    base = dict(repo="acme/shop", issue="12", branch="blacksmith/issue-12",
                loki_url="https://l.example/loki/api/v1/push", loki_user="1",
                prometheus_url="https://p.example/api/prom/push", prometheus_user="2",
                agent_stream=True, secrets=("dop_v1_a.b+c/d", 'tok"quote', "short"))
    base.update(over)
    return AlloySettings(**base)


def test_redaction_pattern_escapes_metacharacters():
    pat = redaction_pattern(["dop_v1_a.b+c/d", "x" * 7, "backslash\\here"])
    assert "x" * 7 not in pat                      # below the minimum length
    assert "dop_v1_a\\\\.b\\\\+c/d" in pat            # re.escape, then doubled for Alloy
    assert "backslash\\\\\\\\here" in pat


def test_redaction_pattern_longest_first_and_empty():
    assert redaction_pattern(["abcdefgh", "abcdefghij"]).startswith("abcdefghij")
    assert redaction_pattern([]) == ""


def test_settings_from_files_gate():
    conf = {"OBSERVABILITY_ENABLED": "true", "LOKI_URL": "https://l", "LOKI_USER": "1",
            "PROMETHEUS_URL": "", "PROMETHEUS_USER": "", "AGENT_STREAM": "false",
            "REPO": "acme/shop", "ISSUE": "12", "BRANCH": "blacksmith/issue-12"}
    env = {"GRAFANA_CLOUD_TOKEN": "glc_abcdefgh", "BLACKSMITH_GITHUB_TOKEN": "ghp_12345678"}
    s = settings_from_files(conf, env)
    assert s is not None and s.agent_stream is False and s.prometheus_url == ""
    assert set(s.secrets) == {"glc_abcdefgh", "ghp_12345678"}
    assert settings_from_files({**conf, "OBSERVABILITY_ENABLED": "false"}, env) is None
    assert settings_from_files(conf, {}) is None


def test_rendered_config_has_every_stream_and_labels():
    text = render_alloy_config(settings())
    for needle in ('job = "runner"', 'job = "agent"', 'job = "bootstrap"',
                   'replacement = "journal"', 'replacement = "docker"',
                   '/home/blacksmith/repo/.blacksmith/log.md',
                   '/home/blacksmith/.claude/projects/*/*.jsonl',
                   '/var/log/blacksmith-bootstrap.log',
                   'repo = "acme/shop"', 'issue = "12"', 'branch = "blacksmith/issue-12"',
                   'sys.env("GC_LOKI_URL")', 'sys.env("GC_TOKEN")',
                   'prometheus.exporter.unix', 'prometheus.exporter.cadvisor',
                   'sys.env("GC_PROM_URL")'):
        assert needle in text, needle
    assert "glc_" not in text and "dop_v1_a.b+c/d" not in text   # secrets only as escaped regex


def test_agent_stream_and_metrics_can_be_left_out():
    text = render_alloy_config(settings(agent_stream=False, prometheus_url=""))
    assert ".claude/projects" not in text
    assert "prometheus." not in text


def test_agent_lines_are_truncated_not_dropped():
    text = render_alloy_config(settings())
    assert "stage.template" in text and "stage.output" in text
    assert str(MAX_LINE_BYTES) in text
    assert "longer_than" not in text


def test_metrics_keep_list_limits_series():
    text = render_alloy_config(settings())
    assert 'action        = "keep"' in text or 'action = "keep"' in text
    for name in ("node_cpu_seconds_total", "node_memory_MemAvailable_bytes",
                 "container_cpu_usage_seconds_total", "container_memory_working_set_bytes"):
        assert name in text


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
def test_rendered_config_validates_with_alloy(tmp_path):
    cfg = tmp_path / "config.alloy"
    cfg.write_text(render_alloy_config(settings()))
    res = subprocess.run(
        ["docker", "run", "--rm",
         "-e", "GC_LOKI_URL=https://l/push", "-e", "GC_LOKI_USER=1",
         "-e", "GC_PROM_URL=https://p/push", "-e", "GC_PROM_USER=2", "-e", "GC_TOKEN=t",
         "-v", f"{cfg}:/cfg/config.alloy:ro", "grafana/alloy:latest", "validate", "/cfg/config.alloy"],
        capture_output=True, text=True, timeout=600,
    )
    if "Unable to find image" in res.stderr and res.returncode != 0 and "pull" in res.stderr.lower():
        pytest.skip("docker cannot pull grafana/alloy")
    assert res.returncode == 0, res.stdout + res.stderr
