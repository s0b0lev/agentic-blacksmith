"""Best-effort Telegram notifications: stage changes, milestones and review rounds."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

TOKEN_KEY = "TELEGRAM_BOT_TOKEN"
CHAT_KEY = "TELEGRAM_CHAT_ID"
MAX_LENGTH = 4096  # Telegram's limit for one message
TIMEOUT_S = 10

Poster = Callable[[str, bytes], None]


class NotifyError(RuntimeError):
    """A message could not be sent. The text never contains the bot token."""


def _urllib_post(url: str, body: bytes) -> None:
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S):
        pass


@dataclass(frozen=True)
class Telegram:
    token: str
    chat_id: str
    post: Poster = _urllib_post

    def send(self, text: str) -> None:
        body = json.dumps({
            "chat_id": self.chat_id,
            "text": text[:MAX_LENGTH],
            "disable_web_page_preview": True,
        }).encode()
        try:
            self.post(f"https://api.telegram.org/bot{self.token}/sendMessage", body)
        except Exception as exc:
            # The URL carries the token; it may appear in the error of a failed request.
            detail = f"{type(exc).__name__}: {exc}".replace(self.token, "***")
            raise NotifyError(f"telegram sendMessage failed: {detail}") from None


def from_env(env: Mapping[str, str], post: Poster = _urllib_post) -> Telegram | None:
    """A sender when both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set, else None."""
    token = env.get(TOKEN_KEY, "").strip()
    chat_id = env.get(CHAT_KEY, "").strip()
    if not token or not chat_id:
        return None
    return Telegram(token, chat_id, post)


def issue_url(repo: str, issue: int) -> str:
    return f"https://github.com/{repo}/issues/{issue}"


def compose(repo: str, issue: int, title: str, text: str, link: str) -> str:
    """``owner/name#N title`` on the first line, the message, then the issue or PR link."""
    head = f"{repo}#{issue} {title}".strip()
    return f"{head}\n{text}\n{link}"
