"""
---------------
- readiness.py -
---------------

Readiness (27.6): whether a pull request may be merged, as one pure function of what GitHub says
and what udl knows.

`readiness()` is given a pull (`Pull`, what `github_get_pull` answers, as readiness reads it), the
review bot's login (or None), the task and the state of every task it needs, and answers a
`Readiness`: ready or not, why not, and the review bot's items that are not fixed.

`parse_review()` reads the review bot's comment: its items, each with its status, and its
assessment. **An "approve" line never overrides an open item** — clean means no item other than
`fixed` *and* an assessment that is not `request_changes` (the bug thevault's `pr-status` had on
12 Nov: a comment saying "approve" over a table with a PARTIALLY FIXED row).

The bot's comment must be **for this head**: a follow-up names the commit it reviewed, and one
that does not was written after the head's commit (a comment written before it reviewed what was
there then, not what is there now).
"""

# System Imports
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, Protocol

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Local Imports
from udl.automations.events import EventType

# Local Imports
from udl.plugins.apps.orch.models import (
    DONE,
    MERGED,
    Assessment,
    Readiness,
    ReviewItem,
    ReviewStatus,
    ReviewVerdict,
)

__all__ = [
    "BOT_STATUSES",
    "TASK_READY",
    "Check",
    "Comment",
    "Pull",
    "TaskLike",
    "TaskReady",
    "clean",
    "for_head",
    "parse_review",
    "readiness",
]

# (The words a status cell may use, longest first: `NOT FIXED` is a row of its own, and
# `PARTIALLY FIXED` must not read as `FIXED`.)
BOT_STATUSES: Final[tuple[tuple[str, ReviewStatus], ...]] = (
    ("PARTIALLY FIXED", ReviewStatus.PARTIALLY_FIXED),
    ("PARTIALLY ADDRESSED", ReviewStatus.PARTIALLY_FIXED),
    ("MOSTLY FIXED", ReviewStatus.PARTIALLY_FIXED),
    ("LARGELY FIXED", ReviewStatus.PARTIALLY_FIXED),
    ("PARTLY FIXED", ReviewStatus.PARTIALLY_FIXED),
    ("NOT FIXED", ReviewStatus.NOT_FIXED),
    ("NEW ISSUE", ReviewStatus.NEW_ISSUE),
    ("STILL OPEN", ReviewStatus.OPEN),
    ("FIXED", ReviewStatus.FIXED),
    ("RESOLVED", ReviewStatus.FIXED),
    ("ADDRESSED", ReviewStatus.FIXED),
    ("DELIBERATE", ReviewStatus.FIXED),
    ("INFORMATIONAL", ReviewStatus.FIXED),
    ("ACCEPTED", ReviewStatus.FIXED),
)
"""The words the review bot puts in a status cell, and what each one means for readiness."""

_OK_CONCLUSIONS: Final = frozenset({"success", "neutral", "skipped"})
"""The conclusions of a check that does not stop a merge."""

_WORDS: Final = (
    r"(?:not |partially |mostly |largely |partly )?"
    r"(?:fixed|resolved|addressed|deliberate|informational|still open|new issue)"
)
"""The words a status may be written with (`FIXED`, `PARTIALLY FIXED`, `NOT FIXED`...)."""

_VERDICT: Final = re.compile(
    r"^\s*\*\*\s*verdict\s*:\s*(all clear|changes needed)\s*\.?\s*\*\*\s*$", re.IGNORECASE
)
"""The bot's last line, which says what it thinks of the whole pull."""
_OVERALL: Final = re.compile(r"^\s*\*\*\s*overall\s*:\s*([^*]+?)\s*\.?\s*\*\*\s*$", re.IGNORECASE)
"""The bot's first line in a first review (`**Overall: request changes.**`)."""
_FOLLOW_UP: Final = re.compile(r"\bfollow-up\b", re.IGNORECASE)
_SHA: Final = re.compile(r"\b[0-9a-f]{7,40}\b")
_LIST_ITEM: Final = re.compile(r"^\s*(?:(\d+)\.|[-*])\s+(.*)$")
"""A numbered or bulleted line of a status list."""
_BOLD: Final = re.compile(r"^\*\*(.+?)\*\*\s*(.*)$")
"""`**<the bold part>** <what follows it>`."""
_STATUS_FIRST: Final = re.compile(rf"^({_WORDS})\s*(?:\([^)]*\))?\s*:\s*(.*)$", re.IGNORECASE)
"""`<STATUS>: <what the item is>`, how a new-issues list writes one."""
_STATUS_AFTER: Final = re.compile(rf"^(.*?):\s*({_WORDS})\b", re.IGNORECASE)
"""`<what the item is>: <STATUS>`, how an earlier-items list writes one."""

_OLD: Final = datetime(1970, 1, 1, tzinfo=UTC)
"""(A comment with no time sorts first: it is not the latest one.)"""


class TaskLike(Protocol):
    """What readiness needs of a task: what it waits on, and whether it is held (a `Task`
    record, or the shape a test has)."""

    needs: list[str]
    """The tasks that must be merged (or done) before it may be merged."""
    hold: str | None
    """Why it is built now and merged later (None: it is not held)."""


class Check(BaseModel):
    """One of a pull's checks, as readiness reads it (a `CheckInfo`)."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    name: str = ""
    status: str = ""
    conclusion: str | None = None
    started_at: AwareDatetime | None = None


class Comment(BaseModel):
    """A comment on a pull, as readiness reads it (a `CommentInfo`)."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    author: str = ""
    body: str = ""
    created_at: AwareDatetime | None = None


class Pull(BaseModel):
    """A pull, as readiness reads it (a `PullDetail`): whether it is a draft, whether it
    merges, its head and when that head was committed, its checks, and its comments."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    draft: bool = False
    mergeable: bool | None = None
    mergeable_state: str = ""
    head_sha: str = ""
    head_committed_at: AwareDatetime | None = None
    checks: list[Check] = Field(default_factory=list[Check])
    comments: list[Comment] = Field(default_factory=list[Comment])


def parse_review(body: str) -> ReviewVerdict:
    """The review bot's comment as items and an assessment.

    An item is a row of a status table (`| # | Item | Status |`) or a numbered or bulleted line
    (`1. **The item: FIXED.**`, `- **NEW ISSUE:** ...`, `- **Nit:** ...`). An item whose status is
    not one of `BOT_STATUSES` is `open`: a word udl does not know is not a word that says the
    item is done. The assessment is the bot's own verdict line, or `unknown` when it wrote none —
    and an unknown assessment never overrides an open item."""
    lines = body.splitlines()
    return ReviewVerdict(
        items=_table_items(lines) or _listed_items(lines),
        assessment=_assessment(lines),
        reviewed_sha=_reviewed_sha(lines),
    )


def clean(verdict: ReviewVerdict) -> bool:
    """Whether the bot's comment leaves nothing to do: no item other than `fixed`, and an
    assessment that is not `request_changes` (an "approve" line never overrides an open item)."""
    if verdict.assessment is Assessment.REQUEST_CHANGES:
        return False
    return all(item.status is ReviewStatus.FIXED for item in verdict.items)


def for_head(verdict: ReviewVerdict, comment: Comment, pull: Pull) -> bool:
    """Whether the bot's comment is about the pull's head commit.

    A follow-up names the commit it reviewed, so it must be this head (or a short sha of it).
    One that names none was written after the head was pushed — otherwise it reviewed what was
    there before it."""
    if verdict.reviewed_sha is not None:
        return bool(pull.head_sha) and pull.head_sha.startswith(verdict.reviewed_sha)
    if comment.created_at is None:
        return False
    pushed = _pushed_at(pull)
    if pushed is None:
        return False
    return comment.created_at > pushed


def _pushed_at(pull: Pull) -> AwareDatetime | None:
    """When the pull's head was pushed: when its earliest check started (a push is what starts
    them), or when the commit was committed when no check says.

    The commit's own time is not the push's: a harness runs its gates first and pushes minutes
    later, so a comment written in that window reviewed what was there before, not this head."""
    started = [check.started_at for check in pull.checks if check.started_at is not None]
    return min(started) if started else pull.head_committed_at


def readiness(
    pull: Pull,
    *,
    bot: str | None,
    task: TaskLike,
    tasks: Mapping[str, str],
) -> Readiness:
    """Whether `pull` may be merged for `task`, and why not.

    Ready needs all of: not a draft; `mergeable` and `mergeable_state` clean; every check on the
    head concluded `success`, `neutral` or `skipped`; when `bot` is set, its latest comment is
    for this head and clean; every task in `task.needs` merged or done; and `task` not held.
    `tasks` is each task's state by key (a need that is not in it is not merged)."""
    blockers = _blockers(pull, bot=bot, needs=list(task.needs), tasks=tasks)
    held = task.hold is not None and not blockers
    if task.hold is not None:
        blockers = [*blockers, f"it is on hold: {task.hold}"]
    return Readiness(
        ready=not blockers,
        held=held,
        blockers=blockers,
        open_items=_open_items(pull, bot=bot),
    )


def _blockers(
    pull: Pull, *, bot: str | None, needs: list[str], tasks: Mapping[str, str]
) -> list[str]:
    """Why `pull` may not be merged, in the order a person would look at them (before the
    hold, which is udl's own decision and not GitHub's)."""
    found: list[str] = []
    if pull.draft:
        found.append("it is a draft")
    if pull.mergeable is None:
        found.append("GitHub has not worked out whether it merges yet")
    elif not pull.mergeable:
        found.append(f"it does not merge cleanly ({pull.mergeable_state or 'dirty'})")
    elif pull.mergeable_state and pull.mergeable_state != "clean":
        found.append(f"GitHub says it is {pull.mergeable_state}, not clean")
    pending = [check.name for check in pull.checks if check.status != "completed"]
    failed = [
        check.name
        for check in pull.checks
        if check.status == "completed" and (check.conclusion or "") not in _OK_CONCLUSIONS
    ]
    if pending:
        found.append(f"its checks are still running: {', '.join(pending)}")
    if failed:
        found.append(f"its checks failed: {', '.join(failed)}")
    if bot is not None:
        found += _bot_blockers(pull, bot=bot)
    for key in needs:
        state = tasks.get(key)
        if state not in (MERGED, DONE):
            found.append(f"it waits on {key}, which is {state or 'not a task'}")
    return found


def _bot_blockers(pull: Pull, *, bot: str) -> list[str]:
    """What the review bot says about the head, if it has said anything about it."""
    comment = _latest(pull, bot=bot)
    if comment is None:
        return [f"the review bot ({bot}) has not commented on it"]
    verdict = parse_review(comment.body)
    if not for_head(verdict, comment, pull):
        return [f"the review bot's latest comment is not about {pull.head_sha[:7] or 'this head'}"]
    found: list[str] = []
    if verdict.assessment is Assessment.REQUEST_CHANGES:
        found.append("the review bot asks for changes")
    open_items = [item for item in verdict.items if item.status is not ReviewStatus.FIXED]
    if open_items:
        found.append(f"the review bot lists {len(open_items)} open item(s)")
    return found


def _open_items(pull: Pull, *, bot: str | None) -> list[ReviewItem]:
    """The bot's items that are not fixed (empty when there is no bot, or no comment yet)."""
    if bot is None:
        return []
    comment = _latest(pull, bot=bot)
    if comment is None:
        return []
    verdict = parse_review(comment.body)
    if not for_head(verdict, comment, pull):
        return []
    return [item for item in verdict.items if item.status is not ReviewStatus.FIXED]


def _latest(pull: Pull, *, bot: str) -> Comment | None:
    """The bot's latest comment on the pull (the last one it wrote, by time)."""
    theirs = [comment for comment in pull.comments if comment.author == bot]
    if not theirs:
        return None
    return max(theirs, key=lambda comment: comment.created_at or _OLD)


def _assessment(lines: list[str]) -> Assessment:
    """What the bot thinks of the whole pull: its verdict line, or its overall line, or
    `unknown` when it wrote neither."""
    for line in reversed(lines):
        found = _VERDICT.match(line)
        if found:
            return (
                Assessment.APPROVE
                if found[1].lower() == "all clear"
                else Assessment.REQUEST_CHANGES
            )
    for line in lines:
        found = _OVERALL.match(line)
        if found:
            word = found[1].lower()
            if "approve" in word and "change" not in word:
                return Assessment.APPROVE
            if "change" in word:
                return Assessment.REQUEST_CHANGES
    return Assessment.UNKNOWN


def _reviewed_sha(lines: list[str]) -> str | None:
    """The commit a follow-up review names (`after dc1fd19`), or None for a first review."""
    heading = next((line for line in lines if line.strip()), "")
    if not _FOLLOW_UP.search(heading):
        return None
    found = _SHA.findall(heading)
    return found[-1] if found else None


def _table_items(lines: list[str]) -> list[ReviewItem]:
    """The items of the comment's first status table (`| # | Item | Status |`), if it has one.

    A table is read once: a row that happens to say "status" (`` `pr-status` ``) is a row, not
    another table (27.6's review)."""
    for header, rows in _tables(lines):
        column = next(
            (index for index, cell in enumerate(header) if cell.strip("* ").lower() == "status"),
            None,
        )
        if column is None:
            continue
        items: list[ReviewItem] = []
        for cells in rows:
            if column >= len(cells):
                continue
            text = " | ".join(cell for index, cell in enumerate(cells) if index not in (0, column))
            items.append(
                ReviewItem(number=cells[0], text=text.strip(), status=_status(cells[column]))
            )
        return items
    return []


def _tables(lines: list[str]) -> list[tuple[list[str], list[list[str]]]]:
    """Each markdown table in the comment: its header's cells, and its rows' cells (a row that is
    all dashes and colons is the header's rule, not a row)."""
    found: list[tuple[list[str], list[list[str]]]] = []
    at = 0
    while at < len(lines):
        if not lines[at].strip().startswith("|"):
            at += 1
            continue
        header = _cells(lines[at])
        rows: list[list[str]] = []
        at += 1
        while at < len(lines) and lines[at].strip().startswith("|"):
            cells = _cells(lines[at])
            if cells and not all(set(cell) <= set("-: ") for cell in cells):
                rows.append(cells)
            at += 1
        found.append((header, rows))
    return found


def _cells(line: str) -> list[str]:
    """A markdown table line's cells, without the pipes and the padding."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.replace("\\|", "|").strip() for cell in stripped.split("|")]


def _listed_items(lines: list[str]) -> list[ReviewItem]:
    """The items of a numbered or bulleted list, each written `<the item>: <STATUS>` (an
    earlier item) or `<STATUS>: <the item>` (a new issue), in bold or not.

    A bold label with no status in it (`- **Nit:** ...`) is an item too: the bot listed it, and
    only an item it calls fixed is one udl may forget."""
    items: list[ReviewItem] = []
    for line in lines:
        found = _LIST_ITEM.match(line)
        if found is None:
            continue
        number, rest = found[1] or "", found[2].strip()
        plain = rest.replace("**", "").strip()
        first = _STATUS_FIRST.match(plain)
        if first is not None:
            items.append(ReviewItem(number=number, text=first[2].strip(), status=_status(first[1])))
            continue
        after = _STATUS_AFTER.match(plain)
        if after is not None:
            items.append(ReviewItem(number=number, text=after[1].strip(), status=_status(after[2])))
            continue
        label = _BOLD.match(rest)
        if label is not None:
            items.append(ReviewItem(number=number, text=label[1].strip().rstrip(":").strip()))
    return items


def _status(cell: str) -> ReviewStatus:
    """What a status cell says, as a `ReviewStatus`: a word udl does not know is `open`."""
    words = cell.upper()
    for phrase, status in BOT_STATUSES:
        if phrase in words:
            return status
    return ReviewStatus.OPEN


class TaskReady(BaseModel):
    """What orch publishes when a task's pull is ready (27.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    project: str
    """The project the task is in."""
    task: str
    """The task, by key (`27.6`)."""
    pull: str
    """Its pull's URL."""
    sha: str
    """The commit at its head, which is what was found ready."""


TASK_READY: Final = EventType(
    name="orch.task.ready",
    data=TaskReady,
    summary="A task's pull is ready to be merged",
    example=TaskReady(
        project="udl",
        task="27.6",
        pull="https://github.com/yelrom0/udl/pull/60",
        sha="dc1fd19",
    ),
    subject=lambda event: f"Task {event.task} is ready: {event.pull}",
    source="orch",
)
"""What orch publishes when `orch_ready` moves a task to `ready` (27.6)."""
