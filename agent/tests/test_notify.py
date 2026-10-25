import json

import pytest

from blacksmith.notify import (
    CHAT_KEY, MAX_LENGTH, TOKEN_KEY, NotifyError, Telegram, compose, from_env, issue_url,
)


class Poster:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    def __call__(self, url: str, body: bytes) -> None:
        self.calls.append((url, json.loads(body)))
        if self.fail:
            raise self.fail


def test_from_env_needs_both_token_and_chat_id():
    assert from_env({}) is None
    assert from_env({TOKEN_KEY: "123:abc"}) is None
    assert from_env({CHAT_KEY: "-1001"}) is None
    assert from_env({TOKEN_KEY: "  ", CHAT_KEY: "-1001"}) is None


def test_from_env_builds_sender_from_both_values():
    sender = from_env({TOKEN_KEY: " 123:abc ", CHAT_KEY: "-1001"}, post=Poster())
    assert (sender.token, sender.chat_id) == ("123:abc", "-1001")


def test_send_posts_json_to_the_bot_api():
    poster = Poster()
    Telegram("123:abc", "-1001", poster).send("hello")
    assert poster.calls == [(
        "https://api.telegram.org/bot123:abc/sendMessage",
        {"chat_id": "-1001", "text": "hello", "disable_web_page_preview": True},
    )]


def test_send_truncates_to_the_telegram_limit():
    poster = Poster()
    Telegram("t", "c", poster).send("x" * (MAX_LENGTH + 10))
    assert len(poster.calls[0][1]["text"]) == MAX_LENGTH


def test_send_failure_is_a_notify_error_without_the_token():
    err = RuntimeError("POST https://api.telegram.org/bot123:abcdef/sendMessage returned 400")
    with pytest.raises(NotifyError) as exc:
        Telegram("123:abcdef", "c", Poster(fail=err)).send("hello")
    assert "123:abcdef" not in str(exc.value)
    assert "400" in str(exc.value) and "RuntimeError" in str(exc.value)


def test_compose_is_header_body_link():
    text = compose("acme/shop", 12, "Scale widget", "stage build", "https://x/issues/12")
    assert text == "acme/shop#12 Scale widget\nstage build\nhttps://x/issues/12"


def test_compose_without_title_or_repo():
    assert compose("", 12, "", "Round 1", "https://x/pull/5") == "#12\nRound 1\nhttps://x/pull/5"


def test_issue_url():
    assert issue_url("acme/shop", 12) == "https://github.com/acme/shop/issues/12"
