import pytest

from blacksmith.scrub import SECRET_KEYS, SecretLeak, load_secret_values, scrub


def test_scrub_raises_on_secret_value():
    with pytest.raises(SecretLeak):
        scrub("hello sk-ant-1234567890 world", ["other-value-1", "sk-ant-1234567890"])


def test_scrub_message_has_no_value():
    with pytest.raises(SecretLeak) as exc:
        scrub("token=sk-ant-1234567890", ["other-value-1", "sk-ant-1234567890"])
    assert "sk-ant-1234567890" not in str(exc.value)
    assert "#2" in str(exc.value)


def test_scrub_ignores_short_values():
    assert scrub("abc def", ["abc"]) == "abc def"


def test_scrub_accepts_single_use_iterable():
    assert scrub("clean text", iter(["longsecret1"])) == "clean text"


def test_load_secret_values_skips_empty():
    env = {"CLAUDE_CODE_OAUTH_TOKEN": "tok-aaaaaaaa", "FIGMA_API_KEY": "", "DIGITALOCEAN_TOKEN": "do-bbbbbbbb", "OTHER": "x"}
    assert load_secret_values(env) == ("tok-aaaaaaaa", "do-bbbbbbbb")
    assert SECRET_KEYS[0] == "CLAUDE_CODE_OAUTH_TOKEN"


def test_telegram_bot_token_is_a_secret():
    assert "TELEGRAM_BOT_TOKEN" in SECRET_KEYS


def test_grafana_cloud_token_is_a_secret():
    assert "GRAFANA_CLOUD_TOKEN" in SECRET_KEYS
