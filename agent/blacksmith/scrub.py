"""Guard outgoing text against leaking secret values."""

from collections.abc import Iterable, Mapping

SECRET_KEYS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "BLACKSMITH_GITHUB_TOKEN",
    "FIGMA_API_KEY",
    "DIGITALOCEAN_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "GRAFANA_CLOUD_TOKEN",
)

MIN_SECRET_LENGTH = 8


class SecretLeak(RuntimeError):
    pass


def load_secret_values(env: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(env[key] for key in SECRET_KEYS if env.get(key))


def scrub(text: str, values: Iterable[str]) -> str:
    for index, value in enumerate(tuple(values), start=1):
        if len(value) >= MIN_SECRET_LENGTH and value in text:
            raise SecretLeak(f"secret value #{index} found in outgoing text")
    return text
