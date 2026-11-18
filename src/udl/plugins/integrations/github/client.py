"""
-------------
- client.py -
-------------

udl's typed client of the GitHub REST API: one method per call the github integration makes,
each answered with a Pydantic model (models.py), on udl.http's client (so httpx's errors are
the UDLErrors they mean). It needs no vendor SDK: the calls are few, and what a plugin is given
is an HTTP client, not a third-party package.

What GitHub answers an error with becomes the error udl has for it (401: ReauthRequired;
403/429 that are its rate limit: RateLimited, saying when the window opens; other 403 and 404:
NotAllowed / DoesNotExist; 409 and 422 on a merge: NotAllowed, with GitHub's own message; 5xx:
the retry policy, then PluginError). A list GitHub pages is followed by the Link header, and a
link off GitHub's own host is never followed (the header is the one part of a request udl does
not choose).

A request is tried again only when trying it again is safe: a GET that failed on a connection or
a 5xx. A POST, a PATCH or a PUT is never sent twice — GitHub may already have done what it
asked, and a second comment or a second merge is worse than a failure the caller can see.
"""

# System Imports
import importlib.metadata
import logging
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import ClassVar, Final, Literal

# Package Imports
import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    TypeAdapter,
    ValidationError,
)

# Local Imports
from udl.clock import Clock, SystemClock
from udl.errors import (
    ConfigError,
    DoesNotExist,
    NotAllowed,
    PluginError,
    RateLimited,
    ReauthRequired,
    UDLError,
    is_retryable,
)
from udl.http import HttpClientFactory, api_client
from udl.plugins.integrations.github.models import (
    GhCheckRun,
    GhComment,
    GhMerge,
    GhPull,
    GhRepo,
    GhReviewComment,
    GhUser,
)

__all__ = [
    "ChangedPulls",
    "GhRateLimit",
    "GitHubClient",
    "MergeMethod",
    "PullStateFilter",
    "new_client",
    "rate_limit_of",
    "user_agent",
]

log = logging.getLogger(__name__)

TOOL: Final = "github"
DEFAULT_API_URL: Final = "https://api.github.com"
ACCEPT: Final = "application/vnd.github+json"
API_VERSION: Final = "2022-11-28"
PAGE_SIZE: Final = 100
# No list is longer than this: it keeps a paging bug (or a Link header that loops) from running
# for ever. GitHub's own longest list is far shorter.
MAX_PAGES: Final = 100
# What a rate limit says when it says nothing else (the fallback of last resort).
A_MINUTE: Final = 60.0
# A repository, as GitHub names one: an owner, a slash, a name (no more, so nothing can climb
# out of /repos/ with a ".." segment or reach another endpoint).
_REPO: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*$")

_REAUTH_HINT: Final = (
    "GitHub refused the token: check it is current, and that it may write pull requests to the "
    "repository (connect the account again: `udl accounts connect github <name>`)."
)
_RATE_HINT: Final = "GitHub is limiting requests. Wait for the time it gives, then try again."
_PERMISSION_HINT: Final = (
    "The token may not do this: a fine-grained token needs Pull requests: write access to the "
    "repository (and Actions: write to rerun its jobs)."
)
_NOT_THERE_HINT: Final = "Check the repository (owner/name) and the number."

type MergeMethod = Literal["merge", "squash", "rebase"]
"""How to merge a pull request: a merge commit, squash, or rebase."""

type PullStateFilter = Literal["open", "closed", "all"]
"""Which pull requests to list."""

type PullStateChange = Literal["open", "closed"]
"""What a pull request's state may be changed to."""


def user_agent() -> str:
    """What udl calls itself to GitHub (GitHub asks for a User-Agent on every request)."""
    return f"udl/{importlib.metadata.version('udl')}"


class GhRateLimit(BaseModel):
    """What GitHub's rate-limit headers say about the token's requests."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    limit: int
    """How many requests the token may make in the window."""
    remaining: int
    """How many it has left."""
    reset: AwareDatetime
    """When the window starts again."""


class _CheckRuns(BaseModel):
    """What a commit's check-runs call answers: a count, and the runs on the page read."""

    model_config = ConfigDict(extra="ignore")

    total_count: int = 0
    check_runs: list[GhCheckRun] = Field(default_factory=list[GhCheckRun])


_OBJECT: Final = TypeAdapter[dict[str, JsonValue]](dict[str, JsonValue])
_ANY_JSON: Final = TypeAdapter[JsonValue](JsonValue)


def repo_name(repo: str) -> str:
    """`repo` as owner/name, checked. Raises ConfigError for anything else, so a value from
    config (or an agent's request) cannot climb out of /repos/ or reach another endpoint."""
    if not _REPO.match(repo):
        raise ConfigError(
            f"{repo!r} is not a repository: it is owner/name (e.g. udl/udl)",
            hint="Check the repository's name.",
        )
    return repo


class ChangedPulls(BaseModel):
    """A repository's pull requests, when GitHub says they changed."""

    pulls: list[GhPull] | None
    """The pulls, most recently updated first (None: GitHub answered 304, nothing changed)."""
    etag: str | None
    """What to give as the ETag next time (None: GitHub sent none, so every look reads)."""


class GitHubClient:
    """A client of the GitHub REST API for one token (None: unauthenticated, which sees only
    public repositories). Made per integration, or per account's client; it holds no state but
    the rate limit GitHub last reported."""

    max_pages: ClassVar[int] = MAX_PAGES
    """The most pages a list reads (a Link header that loops stops there)."""
    retries: ClassVar[int] = 3
    """How many times a GET that failed on a connection or a 5xx is tried again."""
    backoff: ClassVar[tuple[timedelta, ...]] = (
        timedelta(seconds=1),
        timedelta(seconds=2),
        timedelta(seconds=4),
    )
    """How long each retry waits after the last, on the Clock."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_API_URL,
        token: SecretStr | None,
        http: HttpClientFactory,
        clock: Clock | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._http = http
        self._clock = clock or SystemClock()
        self._rate_limit: GhRateLimit | None = None

    @property
    def token(self) -> SecretStr | None:
        """The token requests are authenticated with (None: no token)."""
        return self._token

    @property
    def rate_limit(self) -> GhRateLimit | None:
        """The rate limit GitHub last reported, of any answer (None: it has not reported one)."""
        return self._rate_limit

    # -- the calls -----------------------------------------------------------------------------

    async def user(self) -> GhUser:
        """The account the token belongs to (GET /user)."""
        return await self._get("/user", GhUser)

    async def get_repo(self, owner: str, repo: str) -> GhRepo:
        """A repository (GET /repos/{owner}/{repo}). Raises DoesNotExist when there is none, or
        the token cannot see it. Without a token GitHub says less of it (no permissions), which
        the model allows."""
        return await self._get(f"/repos/{repo_name(f'{owner}/{repo}')}", GhRepo)

    async def list_pulls(
        self,
        repo: str,
        *,
        state: PullStateFilter = "open",
        head: str | None = None,
        base: str | None = None,
    ) -> list[GhPull]:
        """The pull requests of `repo` (owner/name) in `state` (open, closed, all), by default
        those whose head is `head` (owner:branch) or whose base is `base`."""
        query = {"state": state}
        if head is not None:
            query["head"] = head
        if base is not None:
            query["base"] = base
        return await self.paginate(f"/repos/{repo_name(repo)}/pulls", GhPull, query=query)

    async def get_pull(self, repo: str, number: int) -> GhPull:
        """One pull request, by number (GET /repos/{repo}/pulls/{number})."""
        return await self._get(f"/repos/{repo_name(repo)}/pulls/{number}", GhPull)

    async def pulls_if_changed(
        self, repo: str, *, state: PullStateFilter = "all", etag: str | None = None
    ) -> ChangedPulls:
        """The pull requests of `repo` (owner/name) that GitHub says changed since the last look,
        most recently updated first: the first page of them, and the ETag to give next time.

        With `etag` (the last answer's), GitHub answers 304 while nothing changed, so a watcher
        asks once per repository and reads nothing: `pulls` is None then. The first page holds
        the most recently updated pulls, so a pull that was just merged or closed is in it."""
        query = {"state": state, "sort": "updated", "direction": "desc", "per_page": str(PAGE_SIZE)}
        asked = {"If-None-Match": etag} if etag is not None else None
        url = f"{self.base_url}/repos/{repo_name(repo)}/pulls"
        answer, response = await self._ask("GET", url, params=query, headers=asked)
        if response.status_code == 304:
            return ChangedPulls(pulls=None, etag=etag)
        return ChangedPulls(
            pulls=self._each(GhPull, answer, url), etag=response.headers.get("ETag")
        )

    async def open_pull(
        self,
        repo: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str | None,
        draft: bool = False,
    ) -> GhPull:
        """Open a pull request of `head` (owner:branch) into `base` (POST /repos/{repo}/pulls).
        Raises NotAllowed when GitHub refuses it (a branch that is not there, a pull already
        open for that head). Never tried twice: a second POST would be another pull request."""
        sent: dict[str, object] = {"head": head, "base": base, "title": title, "body": body}
        if draft:
            sent["draft"] = True
        return await self._send("POST", f"/repos/{repo_name(repo)}/pulls", sent, GhPull)

    async def update_pull(
        self,
        repo: str,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        state: PullStateChange | None = None,
    ) -> GhPull:
        """Change a pull request's title, body or state (PATCH /repos/{repo}/pulls/{number});
        what is None is left as it is."""
        sent: dict[str, object] = {
            name: value
            for name, value in (("title", title), ("body", body), ("state", state))
            if value is not None
        }
        return await self._send("PATCH", f"/repos/{repo_name(repo)}/pulls/{number}", sent, GhPull)

    async def merge_pull(
        self,
        repo: str,
        number: int,
        *,
        sha: str | None,
        method: MergeMethod = "squash",
    ) -> GhMerge:
        """Merge a pull request (PUT /repos/{repo}/pulls/{number}/merge) as `method` (merge,
        squash, rebase). With `sha`, GitHub refuses unless the head is that commit, so what was
        reviewed is what is merged. Raises NotAllowed, with GitHub's own message, when it will
        not merge (or answers 200 saying it did not). Never tried twice."""
        sent: dict[str, object] = {"merge_method": method}
        if sha is not None:  # (GitHub refuses a merge unless the head is that commit)
            sent["sha"] = sha
        found = await self._send(
            "PUT", f"/repos/{repo_name(repo)}/pulls/{number}/merge", sent, GhMerge
        )
        if not found.merged:
            raise NotAllowed(f"GitHub did not merge the pull request ({found.message})")
        return found

    async def list_check_runs(self, repo: str, sha: str) -> list[GhCheckRun]:
        """Every check run of a commit (GET /repos/{repo}/commits/{sha}/check-runs), all its
        pages: a commit of a matrix build has more than one pageful, and a run left behind
        could be the one that failed."""
        path = f"/repos/{repo_name(repo)}/commits/{sha}/check-runs"
        total = 0

        def read(body: JsonValue, where: str) -> list[GhCheckRun]:
            nonlocal total
            found = self._parsed(_CheckRuns, body, where)
            total = max(total, found.total_count)
            return found.check_runs

        runs = await self._walk(path, read)
        if total > len(runs):
            log.warning("%s: %d check runs, %d read", path, total, len(runs))
        return runs

    async def list_comments(self, repo: str, number: int) -> list[GhComment]:
        """The comments on a pull request's conversation (GET /repos/{repo}/issues/{number}/
        comments)."""
        path = f"/repos/{repo_name(repo)}/issues/{number}/comments"
        return await self.paginate(path, GhComment)

    async def add_comment(self, repo: str, number: int, body: str) -> GhComment:
        """Comment on a pull request (POST /repos/{repo}/issues/{number}/comments). Never tried
        twice: a second POST would post the comment twice."""
        path = f"/repos/{repo_name(repo)}/issues/{number}/comments"
        return await self._send("POST", path, {"body": body}, GhComment)

    async def list_review_comments(self, repo: str, number: int) -> list[GhReviewComment]:
        """The comments on a pull request's diff (GET /repos/{repo}/pulls/{number}/comments)."""
        path = f"/repos/{repo_name(repo)}/pulls/{number}/comments"
        return await self.paginate(path, GhReviewComment)

    async def rerun_failed_jobs(self, repo: str, run_id: int) -> None:
        """Run again the failed jobs of an Actions run (POST /repos/{repo}/actions/runs/{id}/
        rerun-failed-jobs). Raises NotAllowed when it has none that failed, or may not. Never
        tried twice: a second run of the jobs would queue them twice."""
        path = f"/repos/{repo_name(repo)}/actions/runs/{run_id}/rerun-failed-jobs"
        await self._ask("POST", path)

    # -- paging --------------------------------------------------------------------------------

    async def paginate[T: BaseModel](
        self, path: str, model: type[T], *, query: Mapping[str, str] | None = None
    ) -> list[T]:
        """Every item of a list GitHub pages (its Link header's rel="next"), read into `model`.
        At most MAX_PAGES pages are read, and a next link on another host is not followed."""
        return await self._walk(path, lambda body, where: self._each(model, body, where), query)

    async def _walk[T](
        self,
        path: str,
        read: Callable[[JsonValue, str], list[T]],
        query: Mapping[str, str] | None = None,
    ) -> list[T]:
        """Every page of `path` (as `read` reads one), following the Link header. `query` goes
        with the first page; later pages carry the Link header's own."""
        items: list[T] = []
        url = f"{self.base_url}{path}"
        params: dict[str, str] = {"per_page": str(PAGE_SIZE), **(query or {})}
        for _ in range(self.max_pages):
            answer, response = await self._ask("GET", url, params=params)
            items.extend(read(answer, path))
            nxt = self._next(response, url)
            if nxt is None:
                return items
            url, params = nxt
        log.warning("%s: stopped after %d pages", path, self.max_pages)
        return items

    # -- a request -----------------------------------------------------------------------------

    async def _get[T: BaseModel](self, path: str, model: type[T]) -> T:
        answer, _ = await self._ask("GET", f"{self.base_url}{path}")
        return self._parsed(model, answer, path)

    async def _send[T: BaseModel](
        self, method: str, path: str, body: Mapping[str, object], model: type[T]
    ) -> T:
        answer, _ = await self._ask(method, f"{self.base_url}{path}", json=body)
        return self._parsed(model, answer, path)

    def _each[T: BaseModel](self, model: type[T], answer: JsonValue, path: str) -> list[T]:
        """A page of GitHub's JSON as a list of `model`. Raises PluginError when it is not a
        list of that."""
        if not isinstance(answer, list):
            raise PluginError(
                f"GitHub answered {path} with something that is not a list",
                hint="This is udl's bug: the answer needs reading as a page.",
            )
        try:
            return [model.model_validate(item) for item in answer]
        except ValidationError as e:
            raise PluginError(
                f"GitHub answered {path} with something that is not a list of "
                f"{model.__name__} ({e.errors(include_url=False)[0]['msg']})",
                hint="This is udl's bug: the model needs the field GitHub sent.",
            ) from e

    def _parsed[T: BaseModel](self, model: type[T], answer: JsonValue, path: str) -> T:
        """GitHub's JSON as `model`. Raises PluginError when it is not (a shape udl does not
        know, which is a bug here rather than something to retry)."""
        try:
            return model.model_validate(answer)
        except ValidationError as e:
            raise PluginError(
                f"GitHub answered {path} with something that is not {model.__name__} "
                f"({e.errors(include_url=False)[0]['msg']})",
                hint="This is udl's bug: the model needs the field GitHub sent.",
            ) from e

    async def _ask(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[JsonValue, httpx.Response]:
        """A request's answer (as plain JSON) and the response it came in.

        A GET that failed on a connection, or that GitHub answered with a 5xx, is tried again
        (retries times, backoff apart), then PluginError. Any other method is tried once: it may
        already have been done, and doing it twice is worse than saying it failed. An error that
        is not retryable (a 401, a 404, a 403 that is not the rate limit) is raised at once.
        """
        query = dict(params) if params is not None else None
        sent = {
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": user_agent(),
        }
        sent.update(headers or {})
        if self._token is not None:
            sent["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        retryable = method in ("GET", "HEAD")
        failed: UDLError | None = None
        status = 0
        for attempt in range(self.retries + 1 if retryable else 1):
            try:
                # (udl.http's own factory also retries a connection twice; that is its business,
                # and this loop adds a wait between its tries.)
                async with self._http(tool=TOOL) as client:
                    response = await client.request(
                        method, url, params=query, headers=sent, json=json
                    )
            except UDLError as e:
                if not is_retryable(e):
                    raise
                failed = e
            else:
                failed = None
                status = response.status_code
                if status < 500:
                    return self._read(response, url), response
            if attempt == (self.retries if retryable else 0):
                if failed is not None:
                    # (A connection problem, as udl.http raises it: keep what it means, so the
                    # caller knows trying again later may work.)
                    raise failed
                if not retryable:
                    raise PluginError(
                        f"GitHub answered {status} for {method} {url}. It was not tried again, "
                        "since it may already have been done.",
                        hint="Check whether it happened (a pull, a comment) before trying again.",
                    ) from None
                raise PluginError(
                    f"Could not reach GitHub ({url}): GitHub answered {status} "
                    f"{self.retries + 1} times",
                    hint="Check github.com's status, then try again.",
                ) from None
            await self._clock.sleep(self.backoff[attempt])
        raise PluginError(f"Could not reach GitHub ({url})")  # pragma: no cover

    def _read(self, response: httpx.Response, url: str) -> JsonValue:
        """An answer's body, as plain JSON; None for a 204. Raises what an error status means,
        and PluginError for a body that is not JSON (an HTML page from something in between).
        What the answer says of the rate limit is kept either way: a caller reads it after a
        RateLimited too."""
        self._keep(response)
        _raise_for(response, url, self._clock.now())
        if response.status_code == 204 or not response.content:
            return None
        try:
            return _ANY_JSON.validate_json(response.text)
        except ValidationError:
            raise PluginError(
                f"GitHub answered {url} with something that is not JSON "
                f"(HTTP {response.status_code})",
                hint="Check what the API URL points at.",
            ) from None

    def _keep(self, response: httpx.Response) -> None:
        """Remember the rate limit this answer reported, if it reported one."""
        found = rate_limit_of(response)
        if found is not None:
            self._rate_limit = found

    @staticmethod
    def _next(response: httpx.Response, url: str) -> tuple[str, dict[str, str]] | None:
        """The URL (and its own query) of the next page in an answer's Link header, or None when
        it is the last. A link that is not on the API's own host is ignored, with a warning."""
        link = _link_next(response.headers.get("Link"))
        if link is None:
            return None
        if _host(link) != _host(url):
            log.warning("%s: ignored a next link on another host (%s)", url, link)
            return None
        parts = httpx.URL(link)
        return str(parts.copy_with(query=None)), dict(parts.params)


def rate_limit_of(response: httpx.Response) -> GhRateLimit | None:
    """The rate limit in an answer's headers, or None when it has none (or nonsense)."""
    limit = response.headers.get("X-RateLimit-Limit")
    remaining = response.headers.get("X-RateLimit-Remaining")
    reset = response.headers.get("X-RateLimit-Reset")
    if limit is None or remaining is None or reset is None:
        return None
    try:
        return GhRateLimit(
            limit=int(limit),
            remaining=int(remaining),
            reset=datetime.fromtimestamp(int(reset), tz=UTC),
        )
    except ValueError, OverflowError, OSError:
        return None


def new_client(
    token: SecretStr | None,
    *,
    api_url: str = DEFAULT_API_URL,
    http: HttpClientFactory | None = None,
    clock: Clock | None = None,
) -> GitHubClient:
    """A client of GitHub on udl's API client (http: another, for a test's fake)."""
    return GitHubClient(base_url=api_url, token=token, http=http or api_client, clock=clock)


# ---------------------------------------------------------------------------------------------
# What an answer means
# ---------------------------------------------------------------------------------------------


def _raise_for(response: httpx.Response, url: str, now: datetime) -> None:
    """Raise the error an error status means; nothing for a 2xx."""
    status = response.status_code
    if status < 400:
        return
    message = _message(response) or f"GitHub answered {status} for {url}"
    if status == 401:
        raise ReauthRequired(f"GitHub refused the token ({message})", hint=_REAUTH_HINT) from None
    if status == 403 or status == 429:
        if _is_rate_limit(response):
            raise RateLimited(
                f"GitHub is limiting requests ({message})",
                retry_after=_wait(response, now),
                tool=TOOL,
                hint=_RATE_HINT,
            ) from None
        raise NotAllowed(f"GitHub would not allow that ({message})", hint=_PERMISSION_HINT)
    if status == 404:
        raise DoesNotExist(f"GitHub has no such thing ({message})", hint=_NOT_THERE_HINT) from None
    raise NotAllowed(f"GitHub would not do that ({message})")


def _is_rate_limit(response: httpx.Response) -> bool:
    """Whether a 403/429 is GitHub's rate limit rather than a permission: its headers say so
    (a window with none left, or a Retry-After)."""
    if response.headers.get("X-RateLimit-Remaining") == "0":
        return True
    return "retry-after" in {name.lower() for name in response.headers}


def _wait(response: httpx.Response, now: datetime) -> float:
    """How long to wait before asking again, as GitHub's answer says: a Retry-After (seconds or
    a date) if it sent one, else when its rate-limit window opens, else a minute."""
    given = _retry_after(response, now)
    if given is not None:
        return given
    reset = response.headers.get("X-RateLimit-Reset", "").strip()
    if reset.isdigit():
        return max(datetime.fromtimestamp(int(reset), tz=UTC) - now, timedelta()).total_seconds()
    return A_MINUTE


def _retry_after(response: httpx.Response, now: datetime) -> float | None:
    """A Retry-After header (seconds, or a date), as seconds from `now`."""
    value = (response.headers.get("Retry-After") or "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - now).total_seconds(), 0.0)


def _message(response: httpx.Response) -> str:
    """What GitHub's error answer says (its "message"), or nothing when it says none."""
    try:
        found = _OBJECT.validate_json(response.text)
    except ValidationError:
        return ""
    text = found.get("message")
    return text if isinstance(text, str) else ""


def _host(url: str) -> str:
    """A URL's scheme and host, as a Link header is checked against."""
    parts = httpx.URL(url)
    return f"{parts.scheme}://{parts.netloc.decode('ascii', 'replace')}"


def _link_next(value: str | None) -> str | None:
    """The URL of a Link header's rel="next", or None when it has none."""
    if not value:
        return None
    for part in value.split(","):
        target, _, params = part.partition(";")
        if 'rel="next"' in params.replace("'", '"') or "rel=next" in params:
            found = target.strip().removeprefix("<").removesuffix(">")
            return found or None
    return None
