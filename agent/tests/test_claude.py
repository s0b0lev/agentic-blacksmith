from pathlib import Path

from blacksmith.claude import run_claude
from blacksmith.runner import CommandResult

ENV = {"CLAUDE_CODE_OAUTH_TOKEN": "t", "HOME": "/h", "PATH": "/bin"}


def _run(fake_runner, tmp_path, prompt="do the thing", **kw):
    return run_claude(
        prompt,
        cwd=tmp_path,
        max_turns=5,
        timeout_s=60,
        transcript=tmp_path / "logs" / "stage.log",
        run=fake_runner,
        env=ENV,
        **kw,
    )


def test_prompt_goes_to_stdin_not_argv(fake_runner, tmp_path):
    _run(fake_runner, tmp_path, extra_args=["--model", "x"])
    cmd = fake_runner.calls[0]
    assert "do the thing" not in cmd
    assert cmd == [
        "claude", "-p", "--output-format", "json", "--max-turns", "5",
        "--dangerously-skip-permissions", "--model", "x",
    ]
    kw = fake_runner.kwargs[0]
    assert kw["input"] == "do the thing"
    assert kw["cwd"] == tmp_path
    assert kw["timeout"] == 60
    assert kw["env"] == ENV


def test_parses_json_result(fake_runner, tmp_path):
    fake_runner.responses["claude"] = CommandResult(
        0, '{"type":"result","is_error":false,"result":"done","num_turns":7,"total_cost_usd":0.5}', ""
    )
    res = _run(fake_runner, tmp_path)
    assert res.ok and not res.timed_out
    assert res.num_turns == 7
    assert res.result_text == "done"
    assert res.cost_note == "cost_usd=0.5"


def test_is_error_json_not_ok(fake_runner, tmp_path):
    fake_runner.responses["claude"] = CommandResult(0, '{"is_error":true,"result":"boom"}', "")
    assert _run(fake_runner, tmp_path).ok is False


def test_timeout_sets_flag(fake_runner, tmp_path):
    fake_runner.responses["claude"] = CommandResult(124, "", "timed out")
    res = _run(fake_runner, tmp_path)
    assert res.timed_out is True
    assert res.ok is False


def test_transcript_written_even_on_failure(fake_runner, tmp_path):
    fake_runner.responses["claude"] = CommandResult(1, "partial out", "bad thing")
    res = _run(fake_runner, tmp_path)
    text = res.transcript.read_text()
    assert text.startswith("partial out")
    assert "\n\n== stderr ==\nbad thing" in text
    assert res.ok is False


def test_transcript_has_no_stderr_on_success(fake_runner, tmp_path):
    fake_runner.responses["claude"] = CommandResult(0, "{}", "noise")
    res = _run(fake_runner, tmp_path)
    assert res.transcript.read_text() == "{}"


def test_non_json_stdout_is_result_text(fake_runner, tmp_path):
    fake_runner.responses["claude"] = CommandResult(0, "plain words", "")
    res = _run(fake_runner, tmp_path)
    assert res.result_text == "plain words"
    assert res.num_turns is None
    assert res.ok is True
    assert isinstance(res.transcript, Path)


def test_agent_session_never_sees_the_telegram_token():
    from blacksmith.claude import RUNNER_ONLY_ENV, agent_env

    assert "TELEGRAM_BOT_TOKEN" in RUNNER_ONLY_ENV
    env = agent_env({"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "-1",
                     "HOME": "/h"})
    assert env == {"TELEGRAM_CHAT_ID": "-1", "HOME": "/h"}
