"""Best-effort Telegram notifications: one short HTML message per moment worth a glance."""

from __future__ import annotations

import html
import json
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

TOKEN_KEY = "TELEGRAM_BOT_TOKEN"
CHAT_KEY = "TELEGRAM_CHAT_ID"
MAX_LENGTH = 4096  # Telegram's limit for one message
MAX_TITLE = 80  # issue titles longer than this are cut with an ellipsis
MAX_LINE = 1000  # per extra line, so a cut never lands inside an HTML tag or entity
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
            "parse_mode": "HTML",
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


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"


def compose(headline: str, repo: str, issue: int, title: str, lines: Iterable[str] = (),
            link: str = "", label: str = "") -> str:
    """Telegram HTML: a bold headline with the repository name and issue number on the first
    line (what the lock screen shows), the issue title, extra lines, then one labelled link.
    """
    name = repo.rsplit("/", 1)[-1]
    head = f"<b>{html.escape(headline)}</b> \u00b7 {name + ' ' if name else ''}#{issue}"
    parts = [head]
    if title:
        parts.append(html.escape(_cut(title, MAX_TITLE)))
    parts.extend(html.escape(_cut(line, MAX_LINE)) for line in lines if line)
    if link:
        parts.append(f'<a href="{html.escape(link, quote=True)}">{html.escape(label or link)}</a>')
    return "\n".join(parts)
