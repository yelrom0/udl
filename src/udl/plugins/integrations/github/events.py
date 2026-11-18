"""
-------------
- events.py -
-------------

The github integration's events: what happens to a pull request in a watched account. Each is
one of its type's models, with the repository, the pull's number and its page, so a
subscription's filter can say `repo == "acme/widgets" and number == 7` and a notification can
link to it. A watcher publishes each once (its dedupe key names the pull, and what happened to
it), so a udl started again publishes nothing twice.
"""

# System Imports
from collections.abc import Callable, Sequence
from typing import Any, Final

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.automations.events import EventType


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class PullEvent(_Model):
    """A pull request, in a repository."""

    repo: str
    """The repository (owner/name)."""
    number: int
    """The pull request's number."""
    title: str
    """Its title."""
    head_sha: str
    """The commit at its head."""
    html_url: str
    """Its page on GitHub."""

    @property
    def ref(self) -> str:
        """It, as owner/repo#number (what a merge's confirm says)."""
        return f"{self.repo}#{self.number}"


class PullOpened(PullEvent):
    """A pull request was opened."""

    author: str
    """Who opened it."""


class PullUpdated(PullEvent):
    """A commit was pushed to a pull request's head."""

    previous_sha: str
    """The commit its head was before."""


class ChecksFinished(PullEvent):
    """A pull request's checks finished (all of them, which is when its verdict is known)."""

    conclusion: str
    """What they concluded: success or failure."""
    failed: list[str]
    """The names of the checks that failed (empty when they all passed)."""


class PullCommented(PullEvent):
    """Someone commented on a pull request (on its conversation, or on a line of its diff)."""

    author: str
    """Who commented."""
    body: str
    """What they said."""
    is_bot: bool = False
    """Whether it was a bot (a login ending in [bot])."""
    path: str | None = None
    """The file the comment is on (None: it is on the conversation, not on a line of the diff)."""
    line: int | None = None
    """The line of that file (None: on the conversation, or the comment is on the whole file)."""


class PullMerged(PullEvent):
    """A pull request was merged."""

    merged_by: str = ""
    """Who merged it, where GitHub says."""


class PullClosed(PullEvent):
    """A pull request was closed without being merged."""


def _subject(what: str) -> Callable[[PullEvent], str]:
    """A pull's subject: "acme/widgets#7 was opened: Add a widget"."""
    return lambda data: f"{data.ref} {what}: {data.title}"


EVENT_TYPES: Final[Sequence[EventType[Any]]] = (
    EventType(
        name="github.pr.opened",
        data=PullOpened,
        summary="A pull request was opened in a watched repository.",
        example=PullOpened(
            repo="acme/widgets",
            number=7,
            title="Add a widget",
            head_sha="abc1234",
            html_url="https://github.com/acme/widgets/pull/7",
            author="dev",
        ),
        subject=_subject("was opened"),
    ),
    EventType(
        name="github.pr.updated",
        data=PullUpdated,
        summary="A commit was pushed to a pull request's head.",
        example=PullUpdated(
            repo="acme/widgets",
            number=7,
            title="Add a widget",
            head_sha="def5678",
            html_url="https://github.com/acme/widgets/pull/7",
            previous_sha="abc1234",
        ),
        subject=_subject("has a new commit"),
    ),
    EventType(
        name="github.pr.checks_finished",
        data=ChecksFinished,
        summary="A pull request's checks finished (they all have a verdict).",
        example=ChecksFinished(
            repo="acme/widgets",
            number=7,
            title="Add a widget",
            head_sha="abc1234",
            html_url="https://github.com/acme/widgets/pull/7",
            conclusion="failure",
            failed=["pytest"],
        ),
        subject=lambda data: f"{data.ref}: checks {data.conclusion}",
    ),
    EventType(
        name="github.pr.commented",
        data=PullCommented,
        summary="Someone commented on a pull request.",
        example=PullCommented(
            repo="acme/widgets",
            number=7,
            title="Add a widget",
            head_sha="abc1234",
            html_url="https://github.com/acme/widgets/pull/7",
            author="human",
            body="Please fix the test",
        ),
        subject=lambda data: f"{data.ref}: {data.author} commented",
    ),
    EventType(
        name="github.pr.merged",
        data=PullMerged,
        summary="A pull request was merged.",
        example=PullMerged(
            repo="acme/widgets",
            number=7,
            title="Add a widget",
            head_sha="abc1234",
            html_url="https://github.com/acme/widgets/pull/7",
            merged_by="dev",
        ),
        subject=_subject("was merged"),
    ),
    EventType(
        name="github.pr.closed",
        data=PullClosed,
        summary="A pull request was closed without being merged.",
        example=PullClosed(
            repo="acme/widgets",
            number=7,
            title="Add a widget",
            head_sha="abc1234",
            html_url="https://github.com/acme/widgets/pull/7",
        ),
        subject=_subject("was closed"),
    ),
)
