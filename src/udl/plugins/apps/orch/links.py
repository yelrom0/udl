"""
-----------
- links.py -
-----------

Links (Phase 26.4): a typed pointer from a feature, task, question or action to something else —
a pull, an issue, a commit, a branch, a CI run, a file, a URL, a doc, a Stitch screen, or
another task.

`canonical()` is the one rule for what a URL is: a GitHub URL is normalised — its host checked,
its owner and repository lower-cased, and what it points at the only thing kept (a pull's
number, an issue's, a commit's sha, a branch's path) — so one PR is one URL however it was
written, and canonicalising twice changes nothing. `links_in()` scans a text on save: a GitHub
URL or a task key in backticks becomes a link.

A pull's state is refreshed through udl's own `github_get_pull` operation (26.4's service): its
`PullDetail` is what `PullState` describes, and a pull that was merged is `merged`, never
`closed`.
"""

# System Imports
import re
from typing import Final
from urllib.parse import urlsplit, urlunsplit

# Package Imports
from pydantic import BaseModel, ConfigDict, Field

# Local Imports
from udl.automations.events import EventType
from udl.plugins.apps.orch.models import (
    Link,
    LinkKind,
    OwnerKind,
    PullReader,
    PullState,
)
from udl.plugins.apps.orch.records import Link as StoredLink

__all__ = [
    "GITHUB_PR_PATH",
    "GITHUB_URL",
    "LINK_CHANGED",
    "LINK_HOST",
    "LINK_SCHEMES",
    "TASK_KEY",
    "LinkChanged",
    "LinkRecords",
    "canonical",
    "event_url",
    "is_github",
    "is_pull_url",
    "links_in",
    "on_github_pr",
    "pull_ref",
    "refresh_pull",
]

LINK_SCHEMES: Final = ("http", "https")
"""The schemes a link may have (a link is a page, not a script or a file)."""

LINK_HOST: Final = "github.com"
"""The only GitHub host a link is read as: `www.` and `http` are the same site."""

GITHUB_PR_PATH: Final = re.compile(
    r"^/(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+)/pull/(?P<number>[1-9][0-9]*)$"
)
"""The path of a pull's canonical URL: owner, repo and number, nothing after it."""

GITHUB_URL: Final = re.compile(
    r"https?://(?:www\.)?github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+"
    r"/(?:pull|issues|commit|commits|tree|blob)/[^/?#\s]+[^\s]*",
    re.IGNORECASE,
)
"""A GitHub URL in a text: what a sentence writes as a URL, its trailing punctuation left out."""

TASK_KEY: Final = re.compile(r"`(?P<key>[0-9]+(?:\.[0-9]+)+[A-Za-z0-9_.]*)`")
"""A task key in backticks: `26.2`, a number, a dot and a number (the plan's own shape).

A bare `3.14` is the same shape and cannot be told from a key — a spec that must cite a version
should say `version 3.14`, not put it alone in backticks. `v1.2.3` (a letter first) and
`python3.14` (a word) are not keys, and neither is a feature's own key (`F-1`)."""

_TRAILING: Final = ".,;:!?)]}'\""
"""What a sentence puts after a URL that is the sentence's, not the URL's."""

_PULL_KINDS: Final = frozenset({"pull", "issue", "commit", "branch", "ci_run"})
"""The kinds of which a link must be a GitHub URL (a `doc` or a `stitch` screen is not)."""


def _trimmed(matched: str) -> str:
    """`matched` without the punctuation a sentence puts after a URL."""
    return matched.rstrip(_TRAILING)


def _github_parts(url: str) -> tuple[str, str, str, str] | None:
    """A GitHub URL's `(owner, repo, what, rest)`, or None when `url` is not one.

    The host is checked: `evil.example/a/b/pull/1` and `api.github.com/o/r/pull/1` are not
    GitHub, and neither is a `gist.` link. `www.` and `http` are the same site."""
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    if parts.scheme not in LINK_SCHEMES or host != LINK_HOST:
        return None
    bits = parts.path.rstrip("/").strip("/").split("/")
    if len(bits) < 2:
        return None
    return bits[0], bits[1], bits[2] if len(bits) > 2 else "", "/".join(bits[3:])


def canonical(url: str) -> str:
    """`url` as it is stored: a GitHub URL normalised, anything else as it was given.

    A GitHub URL is one thing however it was written — a trailing slash, `/files`, a query, a
    fragment or `http` do not make another URL — its owner and repository are lower-cased (as
    GitHub treats them), a commit's `.patch` is the commit, and an `api.` or `gist.` host is not
    GitHub at all."""
    text = url.strip()
    if text.startswith("udl:"):
        return text
    if "github.com/" not in text.lower():
        return text
    parts = _github_parts(text)
    if parts is None:
        return text
    owner, repo, what, rest = parts
    base = f"/{owner.lower()}/{repo.lower()}"
    if not what:
        return urlunsplit(("https", LINK_HOST, base, "", ""))
    if what in ("pull", "issues") and rest.split("/")[0].removesuffix(".patch").isdigit():
        number = int(rest.split("/")[0].removesuffix(".patch"))
        return urlunsplit(("https", LINK_HOST, f"{base}/{what}/{number}", "", ""))
    if what in ("commit", "commits"):
        sha = rest.split("/")[0].removesuffix(".patch")
        return urlunsplit(("https", LINK_HOST, f"{base}/commit/{sha}", "", ""))
    return urlunsplit(("https", LINK_HOST, f"{base}/{what}/{rest}", "", ""))


def is_github(url: str) -> bool:
    """Whether `url` is a GitHub URL (github.com, www or not, http or https)."""
    return _github_parts(url) is not None


def is_pull_url(url: str) -> bool:
    """Whether `url` is a pull's canonical URL (not an issue's or a commit's)."""
    return pull_ref(url) is not None


def pull_ref(url: str) -> tuple[str, int] | None:
    """The `owner/repo` and number of a pull's canonical URL, or None when it is not one."""
    parts = _github_parts(url)
    if parts is None:
        return None
    owner, repo, what, rest = parts
    if what != "pull":
        return None
    number = rest.split("/")[0]
    if not number.isdigit():
        return None
    return f"{owner}/{repo}", int(number)


def links_in(text: str, *, project: str) -> list[Link]:
    """The links a text makes: each GitHub URL and each task key in backticks, in their order.

    The same URL twice is one link (a PR is one URL). A link found here has no owner yet: it is
    what a feature's or a task's spec becomes when it is saved, not a stored link — the caller
    adds the owner and the project (a task key is `project`'s, so `26.2` of udl is
    `udl:task/udl/26.2`)."""
    found: list[Link] = []
    seen: set[str] = set()

    def add(link: Link) -> None:
        if link.url not in seen:
            seen.add(link.url)
            found.append(link)

    for match in GITHUB_URL.finditer(text):
        url = canonical(_trimmed(match.group(0)))
        kind: LinkKind = "pull" if is_pull_url(url) else "url"
        add(Link(owner_kind=OwnerKind.TASK, owner="", link_kind=kind, url=url))
    for match in TASK_KEY.finditer(text):
        add(
            Link(
                owner_kind=OwnerKind.TASK,
                owner="",
                link_kind="task",
                url=f"udl:task/{project}/{match.group('key')}",
            )
        )
    return found


class LinkChanged(BaseModel):
    """What orch publishes when a link is added, removed or refreshed (26.4's service)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    owner_kind: OwnerKind
    """What the link hangs on."""
    owner: str
    """What it hangs on, by key."""
    url: str
    """The link's canonical URL."""


LINK_CHANGED: Final = EventType(
    name="orch.link.changed",
    data=LinkChanged,
    summary="A link was added, removed or refreshed",
    example=LinkChanged(
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        url="https://github.com/yelrom0/udl/pull/33",
    ),
    subject=lambda event: f"A link on {event.owner_kind} {event.owner}: {event.url}",
    source="orch",
)
"""What orch publishes when one of its links changes (26.4's service)."""


class _Check(BaseModel):
    """One of a pull's checks, as udl's github integration answers it (a CheckInfo)."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    name: str = ""
    status: str = ""
    conclusion: str | None = None


class _Comment(BaseModel):
    """One of a pull's comments (a CommentInfo), as far as a review word goes."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    state: str = ""


class _PullDetail(BaseModel):
    """What a pull link reads of udl's `github_get_pull` answer (a PullDetail): where the pull
    is, its head, its checks and its comments."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    state: str = "open"
    head_sha: str = ""
    checks: list[_Check] = Field(default_factory=list[_Check])
    comments: list[_Comment] = Field(default_factory=list[_Comment])


def _check_word(checks: list[_Check]) -> str:
    """One word for a pull's checks: pass, fail, pending or none (what a link keeps)."""
    if not checks:
        return "none"
    words = {(one.conclusion or one.status).lower() for one in checks}
    if words & {"failure", "timed_out", "cancelled", "action_required"}:
        return "fail"
    if words <= {"success", "neutral", "skipped"}:
        return "pass"
    return "pending"


def _review_word(review: str, comments: list[_Comment]) -> str:
    """One word for a pull's review: approved, changes, pending or none (what a link keeps).

    udl's `github_get_pull` answers a pull's comments, not its reviews, so `review` is `none`
    while no comment carries a state — the pull's own reviews are the github integration's to
    answer (O-C5), and this reads one when it does."""
    if review:
        return review
    states = {one.state.upper() for one in comments if one.state}
    if "CHANGES_REQUESTED" in states:
        return "changes"
    if "APPROVED" in states:
        return "approved"
    return "none"


def _pull_state(detail: object) -> PullState:
    """A `github_get_pull` answer as a pull link's own state: where the pull is, its head, its
    checks and its review (the card's four)."""
    raw = _PullDetail.model_validate(detail)
    return PullState(
        state=raw.state or "open",
        head_sha=raw.head_sha,
        checks=_check_word(raw.checks),
        review=_review_word(_review_of(detail), raw.comments),
    )


def _review_of(detail: object) -> str:
    """The review word an answer carries, if it has one (udl's own `github_get_pull` does not:
    it answers the pull, not its reviews)."""
    if isinstance(detail, BaseModel):
        extra = getattr(detail, "review", "")
        return str(extra) if isinstance(extra, str) else ""
    return ""


async def refresh_pull(link: StoredLink, *, github: PullReader) -> StoredLink:
    """`link` with its state refreshed, or itself when it is not a pull link.

    The state is read through udl's own `github_get_pull` (so a merged pull is `merged`, and its
    checks and review are its own), and kept as a `PullState`."""
    if link.link_kind != "pull":
        return link
    ref = pull_ref(link.url)
    if ref is None:
        return link
    repo, number = ref
    detail = await github.call("github_get_pull", {"repo": repo, "number": number})
    return link.model_copy(update={"state": _pull_state(detail)})


async def on_github_pr(
    links: list[StoredLink], *, event: object, github: PullReader
) -> list[StoredLink]:
    """The links a `github.pr.*` event refreshes: every `pull` link whose URL is the event's
    pull, with its state read again (the others are left as they are).

    The event is udl's own `PullEvent` data (a repo, a number, its html_url), not GitHub's raw
    webhook: `event_url()` reads the pull's page from either, and an event with no page changes
    nothing."""
    url = event_url(event)
    if not url:
        return []
    changed: list[StoredLink] = []
    for link in links:
        if link.link_kind == "pull" and canonical(link.url) == url:
            changed.append(await refresh_pull(link, github=github))
    return changed


class _PullPage(BaseModel):
    """What a `github.pr.*` event says about the pull it is about (udl's `PullEvent`)."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    html_url: str = ""


class _PullEvent(BaseModel):
    """A `github.pr.*` event, as udl's or a webhook's payload has it (an `AppEvent`'s own data
    under `data`, a webhook's page under `pull_request`)."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    data: _PullPage | None = None
    html_url: str = ""
    pull_request: _PullPage | None = None


def event_url(event: object) -> str:
    """The URL of the pull a `github.pr.*` event is about, or an empty string.

    udl's `PullEvent` has an `html_url`; a webhook's own payload has the page under
    `pull_request`. Neither, or a null pull request, is no URL."""
    read = _PullEvent.model_validate(event)
    page = (read.data or read).html_url or (read.pull_request or _PullPage()).html_url
    return canonical(page) if page else ""


# (The store is orch.records': one copy, so a save and the service use one rule. It is imported
# here for a caller that works with links — `from orch.links import LinkRecords`.)
from udl.plugins.apps.orch.records import LinkRecords  # noqa: E402

__all__ += ["LinkRecords"]
