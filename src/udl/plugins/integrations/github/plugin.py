"""
-------------
- plugin.py -
-------------

The github integration: the pull requests of the repositories an account names, as operations
(list them, read one, open one, comment, run a pull's failed checks again, merge it), and as a
watcher that publishes what happens to them (github.pr.opened, .updated, .checks_finished,
.commented, .merged and .closed), each once, reading each repository again only when GitHub
says it changed (the pulls' ETag) and each pull only when it was updated.

An account is connected with a fine-grained personal access token (ConnectMethod.TOKENS): it
must be able to see each of the account's repositories and to write their pull requests, which
connecting it checks (identify), so an account that is connected can open a pull and merge one.
The token is kept in the secret store, never in the account.

Merging is no MCP tool (OperationHints.mcp=False): it is destructive and the person's own, so
an agent is never offered it, and it refuses an MCP or automation caller itself.
"""

# System Imports
import hashlib
import json
import re
from collections.abc import Sequence
from datetime import timedelta
from typing import Annotated, Any, Final

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, SecretStr

# Local Imports
from udl.automations.events import EventType
from udl.clock import pause
from udl.errors import ConfigError, NotAllowed, ReauthRequired, UDLError
from udl.plugins.integrations.base import (
    AccountHandle,
    AccountHealth,
    AccountIdentity,
    AccountOptions,
    ConnectMethod,
    Credentials,
    Duration,
    EventSink,
    FileOnly,
    Integration,
    IntegrationSettings,
    ItemsPage,
    OperationHints,
    StateStore,
    integration_operation,
)
from udl.plugins.integrations.github.client import (
    DEFAULT_API_URL,
    GitHubClient,
    MergeMethod,
    PullStateFilter,
    new_client,
    repo_name,
)
from udl.plugins.integrations.github.events import (
    EVENT_TYPES,
    ChecksFinished,
    PullClosed,
    PullCommented,
    PullEvent,
    PullMerged,
    PullOpened,
    PullUpdated,
)
from udl.plugins.integrations.github.models import (
    GhCheckRun,
    GhComment,
    GhCommit,
    GhPull,
    GhRepo,
    GhReviewComment,
)

__all__ = ["GitHubIntegration", "GitHubOptions", "GitHubSettings", "GitHubState", "GitHubToken"]

# An Actions run's page, which is where a check run's id is: /actions/runs/12345.
_RUN: Final = re.compile(r"/actions/runs/(\d+)")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


# ---------------------------------------------------------------------------------------------
# What an account is
# ---------------------------------------------------------------------------------------------


class GitHubSettings(IntegrationSettings):
    """The github integration's settings."""

    poll_every: Duration = timedelta(seconds=60)
    """How often the watcher asks each repository what changed."""


class GitHubOptions(AccountOptions):
    """A github account's options."""

    repos: list[Annotated[str, Field(pattern=r"^[^/\s]+/[^/\s]+$")]] = Field(
        default_factory=list[Annotated[str, Field(pattern=r"^[^/\s]+/[^/\s]+$")]]
    )
    """The repositories it works in (owner/name, e.g. acme/widgets): its operations and its
    watcher only ever touch these."""
    api_url: Annotated[str, FileOnly()] = DEFAULT_API_URL
    """Where GitHub's API is (set in the config file only: it is not an agent's to change)."""


class GitHubToken(BaseModel):
    """The token a github account is connected with."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    token: SecretStr
    """A fine-grained personal access token for the account's repositories, with Pull requests:
    read and write (and Actions: write, to run a pull's failed checks again)."""


class PullCursor(BaseModel):
    """What the watcher last saw of a pull request."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    head_sha: str
    """The commit at its head."""
    state: str
    """Where it was: open, closed or merged."""
    updated_at: AwareDatetime | None = None
    """When GitHub last said it changed (None: it did not say)."""
    checks: str = ""
    """A digest of its checks, so they are published when they change."""
    comments: str = ""
    """A digest of its conversation's comments, so a new one is published."""
    review: str = ""
    """A digest of the comments on its diff, so a new one is published."""
    checks_etag: str | None = None
    """The ETag of its head's checks, so asking about them again costs no rate limit."""
    comments_etag: str | None = None
    """The ETag of its conversation's comments (see checks_etag)."""
    review_etag: str | None = None
    """The ETag of the comments on its diff (see checks_etag)."""
    checks_paged: bool = False
    """Whether its checks took more than a page, so page one's ETag says nothing (read whole)."""
    comments_paged: bool = False
    """Whether its conversation took more than a page (see checks_paged)."""
    review_paged: bool = False
    """Whether its diff's comments took more than a page (see checks_paged)."""


class GitHubState(BaseModel):
    """The watcher's cursor: each repository's ETag, and each pull request it has seen."""

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)

    etags: dict[str, str] = Field(default_factory=dict[str, str])
    """The ETag of each repository's last answer, so an unchanged one is a 304."""
    pulls: dict[str, PullCursor] = Field(default_factory=dict[str, PullCursor])
    """What it last saw of each pull, by owner/name#number."""


# ---------------------------------------------------------------------------------------------
# What the operations answer
# ---------------------------------------------------------------------------------------------


class PullInfo(_Model):
    """A pull request."""

    repo: str
    """The repository (owner/name)."""
    number: int
    """Its number."""
    title: str
    """Its title."""
    state: str
    """Where it is: open, closed or merged."""
    draft: bool
    """Whether it is a draft."""
    merged: bool
    """Whether it was merged."""
    head: str
    """The branch its head is."""
    head_sha: str
    """The commit at its head."""
    base: str
    """The branch it is into."""
    author: str
    """Who opened it."""
    html_url: str
    """Its page on GitHub."""
    updated_at: AwareDatetime | None = None
    """When GitHub last said it changed."""

    @classmethod
    def of(cls, repo: str, pull: GhPull) -> PullInfo:
        """`pull`, of `repo`, as udl shows it."""
        return cls(
            repo=repo,
            number=pull.number,
            title=pull.title,
            state=_state_of(pull),
            draft=pull.draft,
            merged=pull.merged,
            head=pull.head.ref,
            head_sha=pull.head.sha,
            base=pull.base.ref,
            author=pull.user.login,
            html_url=pull.html_url,
            updated_at=pull.updated_at,
        )


class CheckInfo(_Model):
    """One of a commit's checks."""

    name: str
    """Its name."""
    status: str
    """Where it is: queued, in_progress or completed."""
    conclusion: str | None = None
    """What it concluded, once it has (success, failure, neutral, skipped...)."""
    started_at: AwareDatetime | None = None
    """When it started (None: GitHub did not say). The earliest of a head's checks is when its
    push was: what a review bot's comment without a sha is compared against (27.6)."""

    @classmethod
    def of(cls, run: GhCheckRun) -> CheckInfo:
        return cls(
            name=run.name,
            status=run.status,
            conclusion=run.conclusion,
            started_at=run.started_at,
        )


class CommentInfo(_Model):
    """A comment on a pull request."""

    id: int
    """Its id (GitHub's)."""
    author: str
    """Who wrote it."""
    body: str
    """What they said."""
    is_bot: bool = False
    """Whether it was a bot (a login ending in [bot])."""
    created_at: AwareDatetime | None = None
    """When it was written."""

    @classmethod
    def of(cls, comment: GhComment) -> CommentInfo:
        return cls(
            id=comment.id,
            author=comment.user.login,
            body=comment.body,
            is_bot=_is_bot(comment.user.login),
            created_at=comment.created_at,
        )


def _committed_at(commit: GhCommit | None) -> AwareDatetime | None:
    """When `commit` was committed, or None when GitHub did not say (or there is no commit)."""
    if commit is None or commit.commit.committer is None:
        return None
    return commit.commit.committer.date


class ReviewCommentInfo(CommentInfo):
    """A comment on a line of a pull request's diff."""

    path: str = ""
    """The file it is on."""
    line: int | None = None
    """The line it is on."""

    @classmethod
    def on_a_line(cls, comment: GhReviewComment) -> ReviewCommentInfo:
        """`comment`, which is on a line of the diff."""
        return cls(**CommentInfo.of(comment).model_dump(), path=comment.path, line=comment.line)


class PullDetail(PullInfo):
    """A pull request, with what its page shows."""

    body: str | None = None
    """What it says."""
    mergeable: bool | None = None
    """Whether GitHub says it may be merged (None: it is still working it out)."""
    mergeable_state: str = ""
    """GitHub's own word for that (clean, dirty, behind, blocked...)."""
    head_committed_at: AwareDatetime | None = None
    """When the commit at its head was committed (None: GitHub did not say). A review bot's
    first comment counts only if it was written after this (27.6)."""
    checks: list[CheckInfo] = Field(default_factory=list[CheckInfo])
    """Its checks."""
    comments: list[CommentInfo] = Field(default_factory=list[CommentInfo])
    """The comments on its conversation."""
    review_comments: list[ReviewCommentInfo] = Field(default_factory=list[ReviewCommentInfo])
    """The comments on its diff."""


class Rerun(_Model):
    """What asking for a pull's failed checks to run again did."""

    runs: list[int]
    """The Actions runs asked for again."""
    checks: list[str]
    """The checks that had failed."""


class MergeResult(_Model):
    """What merging a pull request did."""

    merged: bool
    """Whether GitHub merged it."""
    sha: str = ""
    """The commit the merge made."""
    message: str = ""
    """What GitHub said."""


# ---------------------------------------------------------------------------------------------
# What the operations ask for
# ---------------------------------------------------------------------------------------------


class ListPullsRequest(_Model):
    """List a repository's pull requests."""

    repo: str
    """The repository (owner/name), one of the account's."""
    state: PullStateFilter = "open"
    """Which ones: open, closed, or all."""
    head: str | None = None
    """Only those whose head is this branch (a branch name, or owner:branch)."""
    page: str | None = None
    """The page to read, as the last answer's next_page gave it (none: the first)."""
    limit: int = Field(default=25, ge=1, le=100)
    """How many to give at most."""


class GetPullRequest(_Model):
    """Read one pull request, with its checks and comments."""

    repo: str
    """The repository (owner/name), one of the account's."""
    number: int = Field(ge=1)
    """Its number."""


class OpenPullRequest(_Model):
    """Open a pull request (or update the one already open for that head)."""

    repo: str
    """The repository (owner/name), one of the account's."""
    head: str
    """The branch to merge from (a branch of the repository, or owner:branch)."""
    title: str
    """Its title."""
    base: str | None = None
    """The branch to merge into (none: the repository's default branch)."""
    body: str | None = None
    """What it says."""
    draft: bool = False
    """Whether to open it as a draft (a pull already open for that head cannot be made one:
    GitHub's REST API has no such change)."""


class CommentRequest(_Model):
    """Comment on a pull request."""

    repo: str
    """The repository (owner/name), one of the account's."""
    number: int = Field(ge=1)
    """Its number."""
    body: str
    """What to say."""


class RerunRequest(_Model):
    """Run a pull request's failed checks again."""

    repo: str
    """The repository (owner/name), one of the account's."""
    number: int = Field(ge=1)
    """Its number."""


class MergePullRequest(_Model):
    """Merge a pull request."""

    repo: str
    """The repository (owner/name), one of the account's."""
    number: int = Field(ge=1)
    """Its number."""
    sha: str
    """The commit at its head that was reviewed: GitHub refuses unless it is still that one."""
    method: MergeMethod = "squash"
    """How to merge it: merge, squash or rebase."""
    confirm: str
    """Say which pull this is, as owner/repo#number, so a mistyped number merges nothing."""


# ---------------------------------------------------------------------------------------------
# The integration
# ---------------------------------------------------------------------------------------------


class GitHubIntegration(Integration[GitHubSettings, GitHubOptions]):
    """GitHub: the pull requests of the repositories an account names."""

    name = "github"
    title = "GitHub"
    summary = "Pull requests: list them, read one, open one, comment, rerun its checks, merge it."
    settings_model = GitHubSettings
    account_model = GitHubOptions
    connect = (ConnectMethod.TOKENS,)
    token_model = GitHubToken
    state_model = GitHubState

    @classmethod
    def event_types(cls) -> Sequence[EventType[Any]]:
        return EVENT_TYPES

    # -- accounts ------------------------------------------------------------------------------

    async def identify(self, credentials: Credentials, options: GitHubOptions) -> AccountIdentity:
        """Who the token belongs to, once each of the account's repositories is one it may write
        pull requests to. Raises ConfigError when the account names no repository, and NotAllowed
        for a repository it cannot see or may not write."""
        client = self._client_with(credentials, options)
        user = await client.user()
        if not options.repos:
            raise ConfigError(
                "A github account names the repositories it works in",
                hint="Connect it with -O repos=owner/name (a comma-separated list).",
            )
        problems = await self._problems(client, options.repos)
        if problems:
            raise NotAllowed(
                "The token cannot work in every one of the account's repositories",
                hint=" ".join(problems),
            )
        return AccountIdentity(
            id=str(user.id), display=f"{user.login} ({', '.join(options.repos)})"
        )

    async def check(self, account: AccountHandle[GitHubOptions]) -> AccountHealth:
        """Whether the token still works in each of the account's repositories."""
        client = await self._client(account)
        problems = await self._problems(client, account.options.repos)
        return AccountHealth(ok=not problems, problems=problems)

    async def _problems(self, client: GitHubClient, repos: Sequence[str]) -> list[str]:
        """What is wrong with each repository (nothing when it is fine)."""
        problems: list[str] = []
        for repo in repos:
            owner, _, name = repo_name(repo).partition("/")
            try:
                found = await client.get_repo(owner, name)
            except UDLError as e:
                problems.append(f"{repo}: {e.message}")
                continue
            if not _may_write(found):
                problems.append(f"{repo}: the token may not write pull requests there")
        return problems

    # -- the watcher ---------------------------------------------------------------------------

    async def watch(
        self,
        account: AccountHandle[GitHubOptions],
        events: EventSink,
        state: StateStore[Any],
    ) -> None:
        """Watch each of the account's repositories until cancelled: publish what happens to
        their pull requests, each once, saving what it has seen after each look."""
        client = await self._client(account)
        cursor = await state.load()
        found = cursor if isinstance(cursor, GitHubState) else GitHubState()
        while True:
            await pause(self.clock, self.settings.poll_every)
            for repo in account.options.repos:
                found = await self._watch_repo(client, events, found, repo_name(repo))
            await state.save(found)

    async def _watch_repo(
        self, client: GitHubClient, events: EventSink, state: GitHubState, repo: str
    ) -> GitHubState:
        """One look at `repo`: a 304 ends it at once, a pull GitHub did not say changed is left
        alone, and each open pull's checks and comments are asked about again — they change
        without the pull itself changing (a check finishing, a comment, a rerun), and a 304 for
        one that did not costs no rate limit. The first look at a repository is its baseline:
        what is already there is not news."""
        # (The first look is the one with no ETag yet. A repository that had no pulls then is
        # not a baseline for the ones it gets after: `state.etags` says it was looked at, so the
        # first pull opened there is news.)
        first = repo not in state.etags
        changed = await client.pulls_if_changed(repo, etag=state.etags.get(repo))
        if changed.etag is not None:
            state.etags[repo] = changed.etag
        for pull in changed.pulls or []:
            key = f"{repo}#{pull.number}"
            before = state.pulls.get(key)
            if before is not None and before.updated_at == pull.updated_at:
                continue
            state.pulls[key] = _cursor_for(before, pull)
            if first:
                state.pulls[key] = await self._seen(client, repo, pull, state.pulls[key])
                continue
            await self._tell(events, repo, pull, before)
        for key, before in list(state.pulls.items()):
            name, _, number = key.rpartition("#")
            if name != repo or before.state != "open":
                continue
            checks, checks_etag, checks_paged = await client.checks_if_changed(
                repo, before.head_sha, etag=before.checks_etag, paged=before.checks_paged
            )
            comments, comments_etag, comments_paged = await client.comments_if_changed(
                repo, int(number), etag=before.comments_etag, paged=before.comments_paged
            )
            review, review_etag, review_paged = await client.review_comments_if_changed(
                repo, int(number), etag=before.review_etag, paged=before.review_paged
            )
            if checks is None and comments is None and review is None:
                continue  # (three 304s: nothing changed, and they cost no rate limit)
            pull = await client.get_pull(repo, int(number))
            if pull.head.sha != before.head_sha:
                continue  # (its head moved: the next look at the repository says what happened)
            await self._tell(
                events, repo, pull, before, checks=checks, comments=comments, review=review
            )
            update: dict[str, Any] = {
                "state": _state_of(pull),
                "updated_at": pull.updated_at,
                "checks_etag": checks_etag,
                "comments_etag": comments_etag,
                "review_etag": review_etag,
                "checks_paged": checks_paged,
                "comments_paged": comments_paged,
                "review_paged": review_paged,
            }
            if checks is not None:
                update["checks"] = _digest_of(checks)
            if comments is not None:
                update["comments"] = _digest_of(comments)
            if review is not None:
                update["review"] = _digest_of(review)
            state.pulls[key] = before.model_copy(update=update)
        return state

    async def _seen(
        self, client: GitHubClient, repo: str, pull: GhPull, cursor: PullCursor
    ) -> PullCursor:
        """`cursor` with the checks and comments `pull` has now, and nothing published (a
        repository's first look is its baseline)."""
        checks, checks_etag, checks_paged = await client.checks_if_changed(repo, pull.head.sha)
        comments, comments_etag, comments_paged = await client.comments_if_changed(
            repo, pull.number
        )
        review, review_etag, review_paged = await client.review_comments_if_changed(
            repo, pull.number
        )
        return cursor.model_copy(
            update={
                "checks": _digest_of(checks or []),
                "checks_etag": checks_etag,
                "checks_paged": checks_paged,
                "comments": _digest_of(comments or []),
                "comments_etag": comments_etag,
                "comments_paged": comments_paged,
                "review": _digest_of(review or []),
                "review_etag": review_etag,
                "review_paged": review_paged,
            }
        )

    async def _tell(
        self,
        events: EventSink,
        repo: str,
        pull: GhPull,
        before: PullCursor | None,
        *,
        checks: Sequence[GhCheckRun] | None = None,
        comments: Sequence[GhComment] | None = None,
        review: Sequence[GhReviewComment] | None = None,
    ) -> None:
        """Publish what is new about `pull` since `before` (everything, the first time): its
        checks and comments only where they were read (None: not this time)."""
        ref = f"{repo}#{pull.number}"
        if before is None:
            await events.emit(
                "github.pr.opened",
                _event(PullOpened, repo, pull, author=pull.user.login),
                dedupe_key=f"{ref}:opened",
            )
        elif before.head_sha != pull.head.sha:
            await events.emit(
                "github.pr.updated",
                _event(PullUpdated, repo, pull, previous_sha=before.head_sha),
                dedupe_key=f"{ref}:{pull.head.sha}",
            )
        now = _state_of(pull)
        if now != "open" and (before is None or before.state != now):
            if now == "merged":
                await events.emit(
                    "github.pr.merged",
                    _event(PullMerged, repo, pull, merged_by=_merged_by(pull)),
                    dedupe_key=f"{ref}:{now}",
                )
            else:
                await events.emit(
                    "github.pr.closed", _event(PullClosed, repo, pull), dedupe_key=f"{ref}:{now}"
                )
        finished = _concluded(checks) if checks is not None else None
        if finished is not None and (before is None or before.checks != _digest_of(checks or [])):
            conclusion, failed = finished
            await events.emit(
                "github.pr.checks_finished",
                _event(
                    ChecksFinished,
                    repo,
                    pull,
                    conclusion=conclusion,
                    failed=[str(name) for name in failed],
                ),
                dedupe_key=f"{ref}:checks:{_digest_of(checks or [])}",
            )
        for kind, notes, digest in (
            ("comment", comments, before.comments if before is not None else ""),
            ("review", review, before.review if before is not None else ""),
        ):
            if notes is None or (before is not None and digest == _digest_of(notes)):
                continue
            for note in notes:
                await events.emit(
                    "github.pr.commented",
                    _event(
                        PullCommented,
                        repo,
                        pull,
                        author=note.user.login,
                        body=note.body,
                        is_bot=_is_bot(note.user.login),
                        path=note.path if isinstance(note, GhReviewComment) else None,
                        line=note.line if isinstance(note, GhReviewComment) else None,
                    ),
                    dedupe_key=f"{ref}:{kind}:{note.id}",
                )

    # -- the operations ------------------------------------------------------------------------

    @integration_operation(
        "list_pulls",
        title="List pull requests",
        hints=OperationHints(read_only=True, idempotent=True, open_world=True),
        fan_out=True,
    )
    async def list_pulls(
        self, account: AccountHandle[GitHubOptions], request: ListPullsRequest
    ) -> ItemsPage[PullInfo]:
        """List a repository's pull requests, a page at a time: open ones by default, most
        recently updated first."""
        client = await self._client(account)
        repo = _wanted(account.options, request.repo)
        pulls, after = await client.pulls_page(
            repo,
            state=request.state,
            head=_head_ref(repo, request.head),
            page=_page(request.page),
            per_page=request.limit,
        )
        return ItemsPage(items=[PullInfo.of(repo, pull) for pull in pulls], next_page=after)

    @integration_operation(
        "get_pull",
        title="Read a pull request",
        hints=OperationHints(read_only=True, idempotent=True, open_world=True),
    )
    async def get_pull(
        self, account: AccountHandle[GitHubOptions], request: GetPullRequest
    ) -> PullDetail:
        """Read one pull request: where it is, whether it may be merged, its checks, and the
        comments on its conversation and on its diff."""
        client = await self._client(account)
        repo = _wanted(account.options, request.repo)
        pull = await client.get_pull(repo, request.number)
        committed = await client.get_commit(repo, pull.head.sha) if pull.head.sha else None
        checks = await client.list_check_runs(repo, pull.head.sha)
        comments = await client.list_comments(repo, request.number)
        review = await client.list_review_comments(repo, request.number)
        return PullDetail(
            **PullInfo.of(repo, pull).model_dump(),
            body=pull.body,
            mergeable=pull.mergeable,
            mergeable_state=pull.mergeable_state,
            head_committed_at=_committed_at(committed),
            checks=[CheckInfo.of(run) for run in checks],
            comments=[CommentInfo.of(note) for note in comments],
            review_comments=[ReviewCommentInfo.on_a_line(note) for note in review],
        )

    @integration_operation(
        "open_pull",
        title="Open a pull request",
        hints=OperationHints(open_world=True, idempotent=True),
    )
    async def open_pull(
        self, account: AccountHandle[GitHubOptions], request: OpenPullRequest
    ) -> PullInfo:
        """Open a pull request of a branch into another (the repository's default branch unless
        one is named). When one is already open for that head, its title and body are updated."""
        client = await self._client(account)
        repo = _wanted(account.options, request.repo)
        head = _head_ref(repo, request.head) or request.head
        opened = await client.list_pulls(repo, state="open", head=head)
        if opened:
            if request.draft and not opened[0].draft:
                raise NotAllowed(
                    f"{repo}#{opened[0].number} is open and is not a draft",
                    hint=(
                        "GitHub's REST API cannot turn a pull request into a draft; do it on "
                        "github.com, or open a new one as a draft"
                    ),
                )
            found = await client.update_pull(
                repo,
                opened[0].number,
                title=request.title,
                body=request.body,
                base=request.base,
            )
        else:
            base = request.base
            if base is None:
                owner, _, name = repo.partition("/")
                base = (await client.get_repo(owner, name)).default_branch
            found = await client.open_pull(
                repo,
                head=head,
                base=base,
                title=request.title,
                body=request.body,
                draft=request.draft,
            )
        return PullInfo.of(repo, found)

    @integration_operation(
        "comment",
        title="Comment on a pull request",
        hints=OperationHints(open_world=True),
    )
    async def comment(
        self, account: AccountHandle[GitHubOptions], request: CommentRequest
    ) -> CommentInfo:
        """Comment on a pull request's conversation."""
        client = await self._client(account)
        repo = _wanted(account.options, request.repo)
        return CommentInfo.of(await client.add_comment(repo, request.number, request.body))

    @integration_operation(
        "rerun_checks",
        title="Rerun a pull's failed checks",
        hints=OperationHints(open_world=True),
    )
    async def rerun_checks(
        self, account: AccountHandle[GitHubOptions], request: RerunRequest
    ) -> Rerun:
        """Run again the failed jobs of a pull request's head: one Actions run per check that
        failed (nothing when they all passed)."""
        client = await self._client(account)
        repo = _wanted(account.options, request.repo)
        pull = await client.get_pull(repo, request.number)
        checks = await client.list_check_runs(repo, pull.head.sha)
        failed = [run for run in checks if _failed(run)]
        runs = sorted({found for run in failed if (found := _run_of(run)) is not None})
        if not runs:
            raise NotAllowed(
                f"{repo}#{request.number} has no failed check to run again",
                hint="Its checks passed, or none has finished yet.",
            )
        for run_id in runs:
            await client.rerun_failed_jobs(repo, run_id)
        return Rerun(runs=runs, checks=[run.name for run in failed])

    @integration_operation(
        "merge_pull",
        title="Merge a pull request",
        hints=OperationHints(destructive=True, open_world=True, admin=True, mcp=False),
    )
    async def merge_pull(
        self, account: AccountHandle[GitHubOptions], request: MergePullRequest
    ) -> MergeResult:
        """Merge a pull request (no MCP tool: a person merges). It refuses unless the head is
        still the commit named, so what was reviewed is what is merged."""
        repo = _wanted(account.options, request.repo)
        expected = f"{repo}#{request.number}"
        if request.confirm != expected:
            raise ConfigError(
                f"Merging {expected} is confirmed by confirm={expected}",
                hint=f"Say which pull it is: confirm={expected}.",
            )
        client = await self._client(account)
        found = await client.merge_pull(
            repo, request.number, sha=request.sha, method=request.method
        )
        return MergeResult(merged=found.merged, sha=found.sha, message=found.message)

    # -- GitHub --------------------------------------------------------------------------------

    async def _client(self, account: AccountHandle[GitHubOptions]) -> GitHubClient:
        """A client of GitHub for `account`'s token, on the API it names."""
        return self._client_with(await account.credentials(), account.options)

    def _client_with(self, credentials: Credentials, options: GitHubOptions) -> GitHubClient:
        if not isinstance(credentials, GitHubToken):
            raise ReauthRequired(
                "A github account is connected with a token, and this one has none",
                hint="Connect it again: `udl accounts connect github <name>`.",
            )
        return new_client(
            credentials.token, api_url=options.api_url, http=self.http, clock=self.clock
        )


# ---------------------------------------------------------------------------------------------
# What GitHub's answers mean
# ---------------------------------------------------------------------------------------------


def _wanted(options: GitHubOptions, repo: str) -> str:
    """`repo`, as the account names it, which it must (GitHub's own names are not
    case-sensitive, and neither is this)."""
    name = repo_name(repo)
    for wanted in options.repos:
        if wanted.casefold() == name.casefold():
            return wanted
    raise NotAllowed(
        f"{name} is not one of this github account's repositories",
        hint=f"Its repositories: {', '.join(options.repos) or 'none'}.",
    )


def _page(page: str | None) -> int:
    """The page a list asks for: the first, or a last answer's next_page (a number)."""
    if page is None:
        return 1
    if not page.isdigit() or int(page) < 1:
        raise ConfigError(
            f"{page!r} is not a page",
            hint="Leave it out for the first page, or give the next_page a last answer had.",
        )
    return int(page)


def _cursor_for(before: PullCursor | None, pull: GhPull) -> PullCursor:
    """What the watcher now knows of `pull`: the pull itself, and `before`'s checks and comments
    (a new head's are dropped: they are another commit's)."""
    if before is None:
        return PullCursor(head_sha=pull.head.sha, state=_state_of(pull), updated_at=pull.updated_at)
    update: dict[str, Any] = {"state": _state_of(pull), "updated_at": pull.updated_at}
    if before.head_sha != pull.head.sha:
        update |= {"head_sha": pull.head.sha, "checks": "", "checks_etag": None}
    return before.model_copy(update=update)


def _merged_by(pull: GhPull) -> str:
    """Who merged a pull, as GitHub said ("" when it did not say)."""
    return pull.merged_by.login if pull.merged_by is not None else ""


def _failed(run: GhCheckRun) -> bool:
    """Whether a check run failed: it concluded, and not by passing (a timeout and a cancelled
    run are failures too, and a rerun runs them again)."""
    return run.conclusion is not None and run.conclusion not in ("success", "neutral", "skipped")


def _event[T: PullEvent](kind: type[T], repo: str, pull: GhPull, **extra: JsonValue) -> T:
    """`kind`'s event about `pull` of `repo`: the fields every pull event carries, and `extra`."""
    fields: dict[str, JsonValue] = {
        "repo": repo,
        "number": pull.number,
        "title": pull.title,
        "head_sha": pull.head.sha,
        "html_url": pull.html_url,
    }
    return kind.model_validate(fields | extra)


def _head_ref(repo: str, head: str | None) -> str | None:
    """`head` as GitHub asks for it: owner:branch (a branch on its own gets its owner)."""
    if head is None or ":" in head:
        return head
    owner, _, _ = repo.partition("/")
    return f"{owner}:{head}"


def _may_write(repo: GhRepo) -> bool:
    """Whether a token may write a repository's pull requests (GitHub says nothing of it to a
    request without a token)."""
    return repo.permissions is not None and repo.permissions.writes_pulls


def _state_of(pull: GhPull) -> str:
    """Where a pull is: merged, closed or open."""
    if pull.merged:
        return "merged"
    return pull.state.value


def _is_bot(login: str) -> bool:
    """Whether a login is a bot's (GitHub's bots are name[bot])."""
    return login.endswith("[bot]")


def _concluded(checks: Sequence[GhCheckRun]) -> tuple[str, list[str]] | None:
    """What the checks concluded, once every one of them has (None while one is running)."""
    if not checks or any(run.status != "completed" for run in checks):
        return None
    failed = [run.name for run in checks if _failed(run)]
    return ("failure" if failed else "success"), failed


def _run_of(run: GhCheckRun) -> int | None:
    """The Actions run a check run is part of (its page says), if GitHub said."""
    found = _RUN.search(run.html_url)
    return int(found.group(1)) if found else None


def _digest_of(items: Sequence[BaseModel]) -> str:
    """A digest of what a list of GitHub's models says, so a change is one comparison."""
    return _digest([item.model_dump(mode="json") for item in items])


def _digest(values: Sequence[JsonValue]) -> str:
    """A short digest of `values`, so a change is one comparison."""
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]
