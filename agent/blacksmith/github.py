"""GitHub client driven entirely through the `gh` CLI (token comes from GH_TOKEN)."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlencode

from blacksmith.runner import CommandResult, CommandRunner
from blacksmith.scrub import scrub

MARKER = "<!-- blacksmith:{kind} -->"
MARKER_PREFIX = "<!-- blacksmith:"
ACCEPT = ["-H", "Accept: application/vnd.github+json"]


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class Comment:
    id: int
    author: str
    body: str
    created_at: str
    kind: str  # "issue" | "review_comment" | "review"
    path: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    author: str
    labels: tuple[str, ...]
    state: str
    comments: tuple[Comment, ...]


@dataclass(frozen=True)
class PR:
    number: int
    url: str
    head: str


def _normalise(timestamp: str) -> str:
    return timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp


def _comment_from_issue_comment(raw: dict) -> Comment:
    return Comment(raw["id"], raw["user"]["login"], raw.get("body") or "", raw["created_at"], "issue")


def _comment_from_review_comment(raw: dict) -> Comment:
    line = raw.get("line") or raw.get("original_line")
    return Comment(
        raw["id"], raw["user"]["login"], raw.get("body") or "", raw["created_at"],
        "review_comment", raw.get("path"), line,
    )


def _comment_from_review(raw: dict) -> Comment:
    return Comment(
        raw["id"], raw["user"]["login"], raw.get("body") or "", raw["submitted_at"], "review"
    )


class GitHub:
    def __init__(self, repo: str, run: CommandRunner, secret_values: Iterable[str] = ()) -> None:
        self.repo = repo
        self._run = run
        self._secrets = tuple(secret_values)
        self._viewer: str | None = None

    def _gh(self, cmd: list[str], input: str | None = None) -> CommandResult:
        result = self._run(cmd, input=input)
        if result.code != 0:
            raise GitHubError(f"{' '.join(cmd)} failed ({result.code}): {result.stderr.strip()}")
        return result

    def _api(self, path: str, method: str | None = None, body: dict | None = None):
        cmd = ["gh", "api", path, *ACCEPT]
        if method:
            cmd += ["--method", method]
        if body is not None:
            cmd += ["--input", "-"]
        result = self._gh(cmd, input=json.dumps(body) if body is not None else None)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def _api_list(self, path: str, params: dict[str, str] | None = None) -> list[dict]:
        if params:
            path = f"{path}?{urlencode(params)}"
        result = self._gh(["gh", "api", "--paginate", "--slurp", path, *ACCEPT])
        pages = json.loads(result.stdout) if result.stdout.strip() else []
        return [item for page in pages for item in page]

    def viewer_login(self) -> str:
        if self._viewer is None:
            self._viewer = self._gh(["gh", "api", "user", "--jq", ".login"]).stdout.strip()
        return self._viewer

    def issue(self, n: int) -> Issue:
        raw = self._api(f"repos/{self.repo}/issues/{n}")
        comments = self._api_list(f"repos/{self.repo}/issues/{n}/comments")
        return Issue(
            number=raw["number"],
            title=raw["title"],
            body=raw.get("body") or "",
            author=raw["user"]["login"],
            labels=tuple(label["name"] for label in raw.get("labels", [])),
            state=raw["state"],
            comments=tuple(_comment_from_issue_comment(c) for c in comments),
        )

    def issue_markdown(self, issue: Issue) -> str:
        text = (
            f"# {issue.title} (#{issue.number})\n\n"
            f"Author: {issue.author}\nLabels: {', '.join(issue.labels)}\n\n{issue.body}\n"
        )
        if issue.comments:
            text += "\n## Comments\n"
            for c in issue.comments:
                text += f"\n### {c.author} at {c.created_at}\n{c.body}\n"
        return text

    def upsert_comment(self, target: int, kind: str, body: str) -> int:
        marker = MARKER.format(kind=kind)
        text = scrub(f"{body}\n\n{marker}", self._secrets)
        existing = self._api_list(f"repos/{self.repo}/issues/{target}/comments")
        found = next((c for c in existing if marker in (c.get("body") or "")), None)
        if found:
            path, method = f"repos/{self.repo}/issues/comments/{found['id']}", "PATCH"
        else:
            path, method = f"repos/{self.repo}/issues/{target}/comments", "POST"
        return self._api(path, method, {"body": text})["id"]

    def create_pr(self, head: str, base: str, title: str, body: str, draft: bool) -> PR:
        title = scrub(title, self._secrets)
        body = scrub(body, self._secrets)
        listing = self._gh([
            "gh", "pr", "list", "--repo", self.repo, "--head", head, "--state", "open",
            "--json", "number,url,headRefName",
        ])
        open_prs = json.loads(listing.stdout or "[]")
        if open_prs:
            existing = open_prs[0]
            self._gh(
                ["gh", "pr", "edit", str(existing["number"]), "--repo", self.repo,
                 "--title", title, "--body-file", "-"],
                input=body,
            )
            return PR(existing["number"], existing["url"], existing["headRefName"])
        cmd = [
            "gh", "pr", "create", "--repo", self.repo, "--head", head, "--base", base,
            "--title", title, "--body-file", "-",
        ]
        if draft:
            cmd.append("--draft")
        url = self._gh(cmd, input=body).stdout.strip().splitlines()[-1]
        return PR(int(url.rsplit("/pull/", 1)[1]), url, head)

    def update_pr_body(self, number: int, body: str) -> None:
        body = scrub(body, self._secrets)
        self._gh(
            ["gh", "pr", "edit", str(number), "--repo", self.repo, "--body-file", "-"], input=body
        )

    def human_comments_since(self, pr: int, since: str | None) -> list[Comment]:
        params = {"since": since} if since else None
        comments = [
            *map(_comment_from_issue_comment,
                 self._api_list(f"repos/{self.repo}/issues/{pr}/comments", params)),
            *map(_comment_from_review_comment,
                 self._api_list(f"repos/{self.repo}/pulls/{pr}/comments", params)),
            *(
                _comment_from_review(r)
                for r in self._api_list(f"repos/{self.repo}/pulls/{pr}/reviews")
                if (r.get("body") or "").strip()
            ),
        ]
        cutoff = _normalise(since) if since else None
        kept = [
            c for c in comments
            if MARKER_PREFIX not in c.body
            and (cutoff is None or _normalise(c.created_at) > cutoff)
        ]
        return sorted(kept, key=lambda c: _normalise(c.created_at))

    def reply(self, comment: Comment, pr: int, body: str) -> None:
        if comment.kind == "review_comment":
            path = f"repos/{self.repo}/pulls/{pr}/comments/{comment.id}/replies"
            text = body
        else:
            first_line = comment.body.splitlines()[0] if comment.body else ""
            path = f"repos/{self.repo}/issues/{pr}/comments"
            text = f"> {first_line}\n\n{body}"
        self._api(path, "POST", {"body": scrub(text, self._secrets)})

    def request_review(self, pr: int, user: str) -> bool:
        if user == self.viewer_login():
            return False
        self._api(f"repos/{self.repo}/pulls/{pr}/requested_reviewers", "POST", {"reviewers": [user]})
        return True

    def dispatch(self, event_type: str, payload: dict) -> None:
        body = {"event_type": event_type, "client_payload": payload}
        scrub(json.dumps(body), self._secrets)
        self._api(f"repos/{self.repo}/dispatches", "POST", body)
