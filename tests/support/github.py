"""
-------------
- github.py -
-------------

FakeGitHub: a small GitHub REST API (an ASGI app) for udl's GitHub client and integration to
run against. It holds state a test changes the way a person would — open a pull, push a commit
to its head, finish a check, comment as the bot, merge — and answers like GitHub: the same
paths and bodies, a Link header on a list, an ETag (so an unchanged answer is a 304), and
rate-limit headers on everything.

Its state is the client's own models (udl.plugins.integrations.github.models), so a test reads
a pull as a GhPull rather than as a dictionary. A test reaches it in-process, through `http()`
(udl's HttpClientFactory) or `client()` (a bare httpx client): nothing goes near the network.
"""

# System Imports
import hashlib
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

# Package Imports
import httpx
from pydantic import TypeAdapter
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

# Local Imports
from udl.db.models import JsonObject
from udl.http import HttpClientFactory
from udl.plugins.integrations.github.models import (
    GhCheckRun,
    GhComment,
    GhPull,
    GhRepo,
    GhReviewComment,
    GhUser,
    PullState,
)

BASE: Final = "https://api.github.com"
"""Where the fake GitHub's API lives (what a client is pointed at)."""
TOKEN: Final = "ghp_fake_token"  # noqa: S105 - (the fake's own)
"""The token the fake accepts (another is refused as GitHub refuses a bad one)."""
LOGIN: Final = "udl-bot"
"""Who that token belongs to."""

_JSON: Final = TypeAdapter[JsonObject](JsonObject)

type Body = JsonObject | list[JsonObject]
"""An answer's body: an object, or a list of them for a page."""
_PAGE: Final = 30
"""The most items a list answers with, when a test has not said otherwise."""


def repo_body(full_name: str) -> GhRepo:
    """A repository as GitHub sends it."""
    return GhRepo(
        full_name=full_name,
        default_branch="main",
        private=True,
        permissions=GhRepo.Permissions(admin=False, push=True, pull=True),
    )


def pull_body(
    repo: str,
    number: int,
    *,
    title: str = "Add a widget",
    head: str = "feat/widgets",
    sha: str = "abc1234",
    base: str = "main",
    base_sha: str = "def5678",
    body: str | None = "It adds a widget",
    draft: bool = False,
    state: PullState = PullState.OPEN,
) -> GhPull:
    """A pull request as GitHub sends it."""
    owner, _, name = repo.partition("/")
    return GhPull(
        number=number,
        title=title,
        state=state,
        draft=draft,
        merged=False,
        mergeable=True,
        mergeable_state="clean",
        head=GhPull.Ref(ref=head, sha=sha),
        base=GhPull.Ref(ref=base, sha=base_sha),
        html_url=f"https://github.com/{owner}/{name}/pull/{number}",
        user=GhUser(login="dev", id=12),
        body=body,
        updated_at=datetime.now(UTC),
    )


def comment_body(comment_id: int, text: str, *, login: str = "human") -> GhComment:
    """A comment on a pull request, as GitHub sends it."""
    return GhComment(
        id=comment_id,
        user=GhUser(login=login, id=11 if login == "human" else 99),
        body=text,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        html_url=f"https://github.com/acme/widgets/pull/7#issuecomment-{comment_id}",
    )


def review_comment_body(
    comment_id: int, text: str, *, path: str = "src/app.py", line: int | None = 42
) -> GhReviewComment:
    """A comment on a line of a pull request's diff."""
    return GhReviewComment(
        **comment_body(comment_id, text).model_dump(),
        path=path,
        line=line,
        commit_id="abc1234",
        in_reply_to_id=None,
    )


def check_run_body(name: str, conclusion: str | None) -> GhCheckRun:
    """A check run of a commit (a conclusion of None: it has not finished)."""
    return GhCheckRun(
        name=name,
        status="completed" if conclusion else "in_progress",
        conclusion=conclusion,
        html_url=f"https://github.com/acme/widgets/runs/{name}",
        completed_at=datetime.now(UTC) if conclusion else None,
    )


def _branch(head: object) -> str:
    """The branch of a head GitHub is given: "owner:branch" or a branch on its own."""
    text = str(head)
    return text.partition(":")[2] or text


def _key(repo: str, number: int) -> str:
    """A pull's key in the fake's maps."""
    return f"{repo}#{number}"


@dataclass
class FakeGitHub:
    """A GitHub API with state: the repositories, their pulls, comments and checks.

    Its fields say how it answers (the token to require, a page size, a status to force); its
    methods change the state, as a person would on github.com. `refuse` makes every call fail as
    a refused connection, and `mangle` rewrites a body, so a client can be tried against
    something GitHub does not send without the fake growing a switch for each case."""

    token: str | None = TOKEN
    """The token requests must send (None: any token, or none)."""
    login: str = LOGIN
    """Who that token belongs to."""
    page_size: int = _PAGE
    """The most items a list answers with, whatever the request asks for (as GitHub caps it)."""
    exhausted: bool = False
    """Whether the token's rate limit is used up (then every call is 403)."""
    rate_limited: bool = False
    """Whether every call is 429, with a Retry-After."""
    now: Callable[[], datetime] | None = None
    """What time it is, for the rate limit's window (None: the system clock). A test that runs a
    client on a FakeClock gives it `clock.now`, so the window it reports is the one that clock
    sees."""
    retry_after: str | None = None
    """What a 429's Retry-After says (None: twelve seconds, as GitHub's secondary limit)."""
    forbidden: bool = False
    """Whether every call is 403 for want of permission (not the rate limit)."""
    refuse: bool = False
    """Whether every call fails as a connection that was refused."""
    fail: dict[str, int] = field(default_factory=dict[str, int])
    """Statuses to force, by "METHOD /path" (e.g. "PUT /repos/a/b/pulls/7/merge": 409)."""
    link_base: str | None = None
    """Where a Link header points (None: here): another host, to try udl's check of it."""
    loop: bool = False
    """Whether every page has a next one (a Link header that loops, to try udl's page cap)."""
    mangle: Callable[[Body], Body] | None = None
    """What to do to every answer's body before it is sent (None: send it as it is)."""
    repos: dict[str, GhRepo] = field(default_factory=dict[str, GhRepo])
    """The repositories, by "owner/name"."""
    pulls: dict[str, list[GhPull]] = field(default_factory=dict[str, list[GhPull]])
    """The pulls of each repository, by "owner/name"."""
    comments: dict[str, list[GhComment]] = field(default_factory=dict[str, list[GhComment]])
    """The conversation comments of each pull, by "owner/name#number"."""
    review_comments: dict[str, list[GhReviewComment]] = field(
        default_factory=dict[str, list[GhReviewComment]]
    )
    """The diff comments of each pull, by "owner/name#number"."""
    checks: dict[str, list[GhCheckRun]] = field(default_factory=dict[str, list[GhCheckRun]])
    """The check runs of each commit, by "owner/name@sha"."""
    merged: list[str] = field(default_factory=list[str])
    """The pulls that were merged ("owner/name#number")."""
    reruns: list[str] = field(default_factory=list[str])
    """The Actions runs whose failed jobs were asked for again."""
    calls: list[str] = field(default_factory=list[str])
    """Every call made, as "METHOD /path?query"."""
    conditionals: list[int] = field(default_factory=list[int])
    """The status of every answer to a request that gave an If-None-Match (304: it changed
    nothing, which costs no rate limit)."""
    sent: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    """The headers of every call made (what udl sends: its token, its User-Agent)."""
    bodies: list[JsonObject] = field(default_factory=list[JsonObject])
    """The JSON body of every call that had one (what udl asks for)."""

    def __post_init__(self) -> None:
        self.repos.setdefault("acme/widgets", repo_body("acme/widgets"))
        self.pulls.setdefault(
            "acme/widgets",
            [
                pull_body("acme/widgets", 7),
                pull_body("acme/widgets", 8, title="Fix the thing", head="fix/thing", sha="999"),
            ],
        )

    # -- the state a test changes ------------------------------------------------------------

    def open_pull(
        self,
        *,
        repo: str = "acme/widgets",
        number: int = 9,
        title: str = "Add a widget",
        head: str = "feat/widgets",
        sha: str = "abc1234",
        body: str | None = "It adds a widget",
        draft: bool = False,
    ) -> GhPull:
        """Add a pull request to `repo`, as GitHub would have it after a push."""
        found = pull_body(repo, number, title=title, head=head, sha=sha, body=body, draft=draft)
        self.pulls.setdefault(repo, []).append(found)
        return found

    def push(self, sha: str, *, repo: str = "acme/widgets", number: int = 7) -> None:
        """Push `sha` to a pull's head (a new commit on it)."""
        pull = self.pull_of(repo, number)
        self.pulls[repo] = [
            pull.model_copy(
                update={
                    "head": pull.head.model_copy(update={"sha": sha}),
                    "updated_at": datetime.now(UTC),
                }
            )
            if item.number == number
            else item
            for item in self.pulls.get(repo, [])
        ]

    def finish_check(self, sha: str, conclusion: str, *, repo: str = "acme/widgets") -> None:
        """Finish the check runs of a commit, as GitHub would after a job ends."""
        self.checks[f"{repo}@{sha}"] = [
            run.model_copy(
                update={
                    "status": "completed",
                    "conclusion": conclusion,
                    "completed_at": datetime.now(UTC),
                }
            )
            for run in self.checks.get(f"{repo}@{sha}", [])
        ]

    def comment(self, text: str, *, repo: str = "acme/widgets", number: int = 7) -> GhComment:
        """Comment on a pull's conversation, as the token's user (the bot)."""
        return self._comment(text, login=self.login, repo=repo, number=number)

    def comment_as(
        self, text: str, *, login: str = "human", repo: str = "acme/widgets", number: int = 7
    ) -> GhComment:
        """Put a comment on a pull's conversation as someone else."""
        return self._comment(text, login=login, repo=repo, number=number)

    def review_comment(
        self,
        text: str,
        *,
        repo: str = "acme/widgets",
        number: int = 7,
        path: str = "src/app.py",
        line: int | None = 42,
    ) -> GhReviewComment:
        """Put a comment on a line of a pull's diff."""
        found = review_comment_body(
            8000 + len(self.review_comments.get(_key(repo, number), [])), text, path=path, line=line
        )
        self.review_comments.setdefault(_key(repo, number), []).append(found)
        return found

    def set_checks(self, sha: str, *runs: GhCheckRun, repo: str = "acme/widgets") -> None:
        """Set the check runs of a commit."""
        self.checks[f"{repo}@{sha}"] = list(runs)

    def merge(self, *, repo: str = "acme/widgets", number: int = 7) -> GhPull:
        """Merge a pull, as GitHub would."""
        pull = self.pull_of(repo, number)
        self.pulls[repo] = [
            pull.model_copy(
                update={
                    "merged": True,
                    "state": PullState.CLOSED,
                    "mergeable": None,
                    "merged_by": GhUser(login=self.login, id=99),
                    "updated_at": datetime.now(UTC),
                }
            )
            if item.number == number
            else item
            for item in self.pulls.get(repo, [])
        ]
        self.merged.append(_key(repo, number))
        return pull.model_copy(
            update={
                "merged": True,
                "state": PullState.CLOSED,
                "merged_by": GhUser(login=self.login, id=99),
            }
        )

    def pull_of(self, repo: str, number: int) -> GhPull:
        """A pull of the fake (fails the test when it has none)."""
        found = next((item for item in self.pulls.get(repo, []) if item.number == number), None)
        assert found is not None, f"the fake has no pull {repo}#{number}"
        return found

    # -- the app -------------------------------------------------------------------------------

    def app(self) -> Starlette:
        """The fake API, as an ASGI app."""
        return Starlette(
            routes=[
                Route("/user", self._user, methods=["GET"]),
                Route("/repos/{owner}/{repo}", self._get_repo, methods=["GET"]),
                Route("/repos/{owner}/{repo}/pulls", self._pulls, methods=["GET", "POST"]),
                Route(
                    "/repos/{owner}/{repo}/pulls/{number:int}",
                    self._one_pull,
                    methods=["GET", "PATCH"],
                ),
                Route(
                    "/repos/{owner}/{repo}/pulls/{number:int}/merge",
                    self._merge,
                    methods=["PUT"],
                ),
                Route(
                    "/repos/{owner}/{repo}/commits/{sha}/check-runs",
                    self._check_runs,
                    methods=["GET"],
                ),
                Route(
                    "/repos/{owner}/{repo}/issues/{number:int}/comments",
                    self._comments,
                    methods=["GET", "POST"],
                ),
                Route(
                    "/repos/{owner}/{repo}/pulls/{number:int}/comments",
                    self._review_comments,
                    methods=["GET"],
                ),
                Route(
                    "/repos/{owner}/{repo}/actions/runs/{run_id:int}/rerun-failed-jobs",
                    self._rerun,
                    methods=["POST"],
                ),
            ]
        )

    def http(self) -> HttpClientFactory:
        """udl's client factory, reaching this fake in-process (and nothing else)."""

        @asynccontextmanager
        async def factory(*, tool: str) -> AsyncGenerator[httpx.AsyncClient]:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(self.app()),
                base_url=BASE,
                headers={"Accept": "application/vnd.github+json"},
            ) as client:
                yield client

        return factory

    def client(self) -> httpx.AsyncClient:
        """A bare httpx client of this fake (for a test that reads a response's headers)."""
        return httpx.AsyncClient(transport=httpx.ASGITransport(self.app()), base_url=BASE)

    # -- the endpoints -------------------------------------------------------------------------

    async def _user(self, request: Request) -> Response:
        return self._answer(request, 200, _json(GhUser(login=self.login, id=99)))

    async def _get_repo(self, request: Request) -> Response:
        repo = self.repos.get(self._name(request))
        if repo is None:
            return self._answer(request, 404, {"message": "Not Found"})
        body = _json(repo)
        if self.token is None:
            # (GitHub says nothing of permissions to a request that has no token.)
            body.pop("permissions", None)
        return self._answer(request, 200, body)

    async def _pulls(self, request: Request) -> Response:
        repo = self._name(request)
        if request.method == "POST":
            body = await self._read(request)
            number = max((pull.number for pull in self.pulls.get(repo, [])), default=0) + 1
            made = pull_body(
                repo,
                number,
                title=_text(body, "title"),
                head=_branch(body["head"]),
                base=_text(body, "base"),
                body=_optional(body, "body"),
                draft=bool(body.get("draft")),
            )
            self.pulls.setdefault(repo, []).append(made)
            return self._answer(request, 201, _json(made))
        state = request.query_params.get("state", "open")
        head = request.query_params.get("head")
        base = request.query_params.get("base")
        wanted = [
            pull
            for pull in self.pulls.get(repo, [])
            if (state == "all" or pull.state is PullState(state))
            and (head is None or pull.head.ref == _branch(head))
            and (base is None or pull.base.ref == base)
        ]
        return self._page(request, [_json(pull) for pull in wanted])

    async def _one_pull(self, request: Request) -> Response:
        repo = self._name(request)
        pull = self.pull_of(repo, int(request.path_params["number"]))
        if request.method == "PATCH":
            body = await self._read(request)
            # (`draft` is not a REST field: GitHub ignores one, and so does the fake, so a
            # client that sends it is not told it worked.)
            changes = {
                name: value
                for name, value in body.items()
                if value is not None and name in ("title", "body", "base", "state")
            }
            base = changes.pop("base", None)
            update: dict[str, object] = dict(changes)
            if base is not None:  # (GitHub is given a branch's name, and answers with the ref)
                update["base"] = pull.base.model_copy(update={"ref": str(base)})
            pull = pull.model_copy(update=update)
            self.pulls[repo] = [
                pull if item.number == pull.number else item for item in self.pulls[repo]
            ]
        return self._answer(request, 200, _json(pull))

    async def _merge(self, request: Request) -> Response:
        repo = self._name(request)
        number = int(request.path_params["number"])
        pull = self.pull_of(repo, number)
        body = await self._read(request)
        sha = body.get("sha")
        if sha is not None and pull.head.sha != sha:
            return self._answer(
                request, 409, {"message": "Head branch was modified, and cannot be merged"}
            )
        if pull.merged:
            return self._answer(request, 405, {"message": "Pull Request is not mergeable"})
        self.merge(repo=repo, number=number)
        return self._answer(
            request,
            200,
            {"merged": True, "sha": "def5678", "message": "Pull Request successfully merged"},
        )

    async def _check_runs(self, request: Request) -> Response:
        repo = self._name(request)
        found = self.checks.get(f"{repo}@{request.path_params['sha']}", [])
        window, link = self._window(request, len(found))
        headers = {"Link": link} if link else None
        return self._answer(
            request,
            200,
            {
                "total_count": len(found),
                "check_runs": [_json(run) for run in found[window[0] : window[1]]],
            },
            headers,
        )

    async def _comments(self, request: Request) -> Response:
        repo = self._name(request)
        number = int(request.path_params["number"])
        if request.method == "POST":
            body = await self._read(request)
            found = self.comment(_text(body, "body"), repo=repo, number=number)
            return self._answer(request, 201, _json(found))
        return self._page(
            request, [_json(note) for note in self.comments.get(_key(repo, number), [])]
        )

    async def _review_comments(self, request: Request) -> Response:
        repo = self._name(request)
        number = int(request.path_params["number"])
        found = self.review_comments.get(_key(repo, number), [])
        return self._page(request, [_json(note) for note in found])

    async def _rerun(self, request: Request) -> Response:
        repo = self._name(request)
        self.reruns.append(f"{repo}@{request.path_params['run_id']}")
        return self._answer(request, 202, {})

    # -- what every answer goes through --------------------------------------------------------

    def _answer(
        self,
        request: Request,
        status: int,
        payload: Body,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Answer with `payload`, as GitHub would: the token checked, `refuse` (a refused
        connection), `fail` (a forced status), the rate limit, and an ETag a repeat GET reuses."""
        if self.refuse:
            raise httpx.ConnectError("connection refused")
        self._record(request)
        if (
            self.token is not None
            and request.headers.get("Authorization") != f"Bearer {self.token}"
        ):
            return self._respond(request, 401, {"message": "Bad credentials"}, self._rate_headers())
        rate = {"Retry-After": self.retry_after} if self.retry_after else {}
        if self.rate_limited:
            return self._respond(
                request,
                429,
                {"message": "You have exceeded a secondary rate limit. Please wait."},
                self._rate_headers() | (rate or {"Retry-After": "12"}),
            )
        if self.exhausted:
            return self._respond(
                request,
                403,
                {"message": "API rate limit exceeded for this token."},
                self._rate_headers() | rate,
            )
        if self.forbidden:
            return self._respond(
                request,
                403,
                {"message": "Resource not accessible by personal access token"},
                self._rate_headers(),
            )
        forced = self.fail.get(f"{request.method} {request.url.path}")
        return self._respond(
            request, forced or status, payload, self._rate_headers() | (headers or {})
        )

    def _respond(
        self,
        request: Request,
        status: int,
        payload: Body,
        headers: dict[str, str],
    ) -> Response:
        """Answer with `payload`, unless the request's If-None-Match is its ETag."""
        body = self.mangle(payload) if self.mangle is not None else payload
        etag = '"' + hashlib.sha256(_dump(body).encode()).hexdigest()[:16] + '"'
        headers["ETag"] = etag
        if request.headers.get("If-None-Match") == etag:
            self.conditionals.append(304)
            return Response(status_code=304, headers=headers)
        return JSONResponse(body, status_code=status, headers=headers)

    def _page(self, request: Request, items: list[JsonObject]) -> Response:
        """One page of a list, with a Link header when there is another (or a previous one)."""
        window, link = self._window(request, len(items))
        headers = {"Link": link} if link else None
        return self._answer(request, 200, items[window[0] : window[1]], headers)

    def _window(self, request: Request, count: int) -> tuple[tuple[int, int], str]:
        """The slice of a list of `count` this page shows, and its Link header ("" when it is
        the only page)."""
        size = min(int(request.query_params.get("per_page", self.page_size)), self.page_size)
        page = max(int(request.query_params.get("page", 1)), 1)
        start = (page - 1) * size
        last = max((count + size - 1) // size, 1)
        path = request.url.path
        base = self.link_base or BASE
        link = ""
        if self.loop:
            page += 1
            link = (
                f'<{base}{path}?page={page}&per_page={size}>; rel="next", '
                f'<{base}{path}?page={page}&per_page={size}>; rel="last"'
            )
        elif count > start + size:
            link = (
                f'<{base}{path}?page={page + 1}&per_page={size}>; rel="next", '
                f'<{base}{path}?page={last}&per_page={size}>; rel="last"'
            )
        elif page > 1:
            link = f'<{base}{path}?page=1&per_page={size}>; rel="prev"'
        return (start, start + size), link

    def _rate_headers(self) -> dict[str, str]:
        """The rate-limit headers GitHub sends on every answer."""
        return {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "0" if (self.exhausted or self.rate_limited) else "4998",
            "X-RateLimit-Reset": str(
                int(((self.now or _utcnow)() + timedelta(minutes=30)).timestamp())
            ),
        }

    async def _read(self, request: Request) -> JsonObject:
        """The JSON body of a request, kept (so a test can see what udl asked for)."""
        body = _JSON.validate_json(await request.body())
        self.bodies.append(body)
        return body

    def _record(self, request: Request) -> None:
        """Keep what a call asked, and what it sent."""
        query = request.url.query
        self.calls.append(f"{request.method} {request.url.path}" + (f"?{query}" if query else ""))
        self.sent.append({name.lower(): value for name, value in request.headers.items()})

    def _name(self, request: Request) -> str:
        """The repository a request is about."""
        return f"{request.path_params['owner']}/{request.path_params['repo']}"

    def _comment(self, text: str, *, login: str, repo: str, number: int) -> GhComment:
        """Put a comment on a pull's conversation (which says, as GitHub does, that it was
        updated: what a watcher's ETag and its pulls' updated_at tell it)."""
        found = comment_body(
            9000 + len(self.comments.get(_key(repo, number), [])), text, login=login
        )
        self.comments.setdefault(_key(repo, number), []).append(found)
        self._touch(repo, number)
        return found

    def _touch(self, repo: str, number: int) -> None:
        """Say a pull was updated (as GitHub does when something happens to it)."""
        self.pulls[repo] = [
            item.model_copy(update={"updated_at": datetime.now(UTC)})
            if item.number == number
            else item
            for item in self.pulls.get(repo, [])
        ]


def _json(model: GhPull | GhComment | GhReviewComment | GhCheckRun | GhRepo | GhUser) -> JsonObject:
    """A model as GitHub sends it (the wire format udl's models read)."""
    body: JsonObject = model.model_dump(mode="json")
    return body


def _text(body: JsonObject, name: str) -> str:
    """A string field of a request's body."""
    value = body.get(name)
    assert isinstance(value, str), f"{name} is not a string"
    return value


def _optional(body: JsonObject, name: str) -> str | None:
    """A string-or-nothing field of a request's body."""
    value = body.get(name)
    return value if isinstance(value, str) else None


def _utcnow() -> datetime:
    """The time now, as GitHub's headers are measured from."""
    return datetime.now(UTC)


def _dump(body: Body) -> str:
    """An answer's body as text, for its ETag."""
    return json.dumps(body, sort_keys=True, default=str)
