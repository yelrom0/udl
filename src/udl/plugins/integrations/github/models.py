"""
-------------
- models.py -
-------------

The GitHub REST models: one per response udl reads (GhUser, GhRepo, GhPull, GhCheckRun,
GhComment, GhReviewComment, GhMerge), named as GitHub names them. A response may leave out
anything udl does not require, so those fields are optional; GitHub's extra fields are ignored,
since it adds them all the time. What udl does require, but a response lacks or sends in
another shape, Pydantic refuses (the client raises it as a PluginError).
"""

# System Imports
from enum import StrEnum

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict

__all__ = [
    "GhCheckRun",
    "GhComment",
    "GhMerge",
    "GhPull",
    "GhRepo",
    "GhReviewComment",
    "GhUser",
    "PullState",
]


class GhModel(BaseModel):
    """The base of the GitHub models: frozen, its fields documented, GitHub's extras ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore", use_attribute_docstrings=True)


class GhUser(GhModel):
    """A GitHub account, as a pull, a comment or /user names it."""

    login: str
    """Its handle (e.g. udl-bot)."""
    id: int
    """Its number, which never changes."""


class GhRepo(GhModel):
    """A repository."""

    full_name: str
    """It as owner/name (e.g. udl/udl)."""
    default_branch: str
    """The branch a pull is opened against by default."""
    private: bool
    """Whether it is private."""
    permissions: GhRepo.Permissions | None = None
    """What the token may do to it (None: GitHub said nothing, as for a request without a
    token)."""

    class Permissions(GhModel):
        """What the authenticated token may do to a repository."""

        admin: bool = False
        """Whether it may administer it."""
        push: bool = False
        """Whether it may push to it (and so open, update and merge pulls)."""
        pull: bool = False
        """Whether it may read it."""

        @property
        def writes_pulls(self) -> bool:
            """Whether the token may write pull requests here (push, or admin)."""
            return self.push or self.admin

    @property
    def may_write_pulls(self) -> bool:
        """Whether the token may write pull requests here (False: GitHub did not say, so udl
        does not assume it may)."""
        return self.permissions is not None and self.permissions.writes_pulls


class PullState(StrEnum):
    """Where a pull request is."""

    OPEN = "open"
    CLOSED = "closed"


class GhCommit(GhModel):
    """A commit, as GitHub sends it (only what udl reads of it)."""

    sha: str = ""
    """Its sha."""
    commit: GhCommit.Details
    """What it is: who wrote and committed it, and when."""

    class Details(GhModel):
        """A commit's own details."""

        committer: GhCommit.When | None = None
        """Who committed it, and when (None: GitHub did not say)."""
        author: GhCommit.When | None = None
        """Who wrote it, and when."""

    class When(GhModel):
        """A person and a time."""

        name: str = ""
        """Their name."""
        date: AwareDatetime | None = None
        """When (None: GitHub did not say)."""


class GhPull(GhModel):
    """A pull request."""

    number: int
    """Its number, unique in its repository."""
    title: str
    """Its title."""
    state: PullState
    """Whether it is open or closed."""
    draft: bool = False
    """Whether it is a draft (not ready to review)."""
    merged: bool = False
    """Whether it has been merged."""
    mergeable: bool | None = None
    """Whether it can be merged (None: GitHub has not worked it out yet)."""
    mergeable_state: str = ""
    """How mergeable it is (clean, dirty, blocked, unstable, unknown)."""
    head: GhPull.Ref
    """The branch the changes are on."""
    base: GhPull.Ref
    """The branch they are to be merged into."""
    html_url: str
    """Its page on github.com."""
    user: GhUser
    """Who opened it."""
    merged_by: GhUser | None = None
    """Who merged it (None: nobody has, or GitHub did not say)."""
    body: str | None = None
    """What it says (None: nothing)."""
    updated_at: AwareDatetime | None = None
    """When it was last changed."""

    class Ref(GhModel):
        """One end of a pull request."""

        ref: str
        """The branch's name."""
        sha: str = ""
        """The commit at its head (GitHub leaves it out where it has none)."""


class GhCheckRun(GhModel):
    """One check run of a commit."""

    name: str
    """Its name (e.g. the job that ran)."""
    status: str
    """queued, in_progress or completed."""
    conclusion: str | None = None
    """success, failure, neutral, cancelled, skipped, timed_out or action_required (None: it has
    not concluded)."""
    html_url: str = ""
    """Its page on github.com."""
    completed_at: AwareDatetime | None = None
    """When it concluded (None: it has not)."""


class GhComment(GhModel):
    """A comment on a pull request (on its conversation, not on a line of its diff)."""

    id: int
    """Its number."""
    user: GhUser
    """Who wrote it (its login says whether it was a bot)."""
    body: str
    """What it says."""
    created_at: AwareDatetime | None = None
    """When it was written."""
    updated_at: AwareDatetime | None = None
    """When it was last edited."""
    html_url: str = ""
    """Its page on github.com."""


class GhReviewComment(GhComment):
    """A comment on a line of a pull request's diff."""

    path: str
    """The file it is on, in the repository."""
    line: int | None = None
    """The line in the file's new version (None: the comment is on a file, not a line)."""
    commit_id: str = ""
    """The commit it was made on."""
    in_reply_to_id: int | None = None
    """The comment it replies to, when it is a reply."""


class GhMerge(GhModel):
    """What GitHub answers a merge with."""

    merged: bool
    """Whether the pull request was merged."""
    sha: str = ""
    """The merge commit (when it was)."""
    message: str = ""
    """What GitHub says (why not, when it was not)."""


GhRepo.model_rebuild()
GhPull.model_rebuild()
