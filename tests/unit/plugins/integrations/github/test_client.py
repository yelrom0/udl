"""
------------------
- test_client.py -
------------------

The typed GitHub client: each call's path, query, auth header and body; how a response is
parsed (and one that is not the model refused); every error mapping the card names; pagination
by the Link header; and the retry policy for 5xx, on the Clock, with the rate limit it reports
kept
"""

# System Imports
import asyncio
import itertools
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Final

# Package Imports
import httpx
import pytest
from pydantic import SecretStr

# Local Imports
from support.clock import FakeClock
from support.github import BASE, TOKEN, Body, FakeGitHub, check_run_body
from support.github import repo_body as gh_repo_body
from udl.errors import (
    ConfigError,
    DoesNotExist,
    NetworkError,
    NotAllowed,
    PluginError,
    RateLimited,
    ReauthRequired,
)
from udl.plugins.downloaders.errors import httpx_error
from udl.plugins.integrations.github.client import (
    DEFAULT_API_URL,
    GitHubClient,
    new_client,
    rate_limit_of,
    user_agent,
)
from udl.plugins.integrations.github.models import GhPull, GhRepo, PullState

REPO: Final = "acme/widgets"
RATE: Final = {"X-RateLimit-Limit": "30", "X-RateLimit-Remaining": "29"}
WAIT: Final = timedelta(minutes=5)
"""How far the clock moves on while a retry waits."""

Answer = Callable[[httpx.Request], httpx.Response]


def api_of(answer: Answer) -> Any:
    """A client factory whose every request is answered by `answer` (for the retry tests: the
    fake's own answers cannot fail a request that way)."""

    @asynccontextmanager
    async def factory(*, tool: str) -> AsyncGenerator[httpx.AsyncClient]:
        async def handle(request: httpx.Request) -> httpx.Response:
            try:
                return answer(request)
            except httpx.HTTPError as e:  # (as udl.http maps them, for a real client)
                raise httpx_error(e, tool=tool) from e

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url=BASE
        ) as client:
            yield client

    return factory


def client_of(gh: FakeGitHub, *, clock: FakeClock | None = None) -> GitHubClient:
    """A client of `gh` on `clock` (one that stands still, by default)."""
    return GitHubClient(
        base_url=BASE,
        token=SecretStr(TOKEN),
        http=gh.http(),
        clock=clock or FakeClock(),
    )


async def moved[T](clock: FakeClock, work: Coroutine[Any, Any, T]) -> T:
    """Run `work`, moving `clock` on whenever it waits: a client's retry sleeps on it, and a
    FakeClock only leaves a sleep when a test moves time on."""
    task = asyncio.create_task(work)
    for _ in range(100):
        if task.done():
            return task.result()
        await clock.advance(WAIT)
    task.cancel()
    raise AssertionError(f"{work} never finished")


@pytest.fixture
def gh() -> FakeGitHub:
    """A GitHub with a token, a repository and two open pulls."""
    return FakeGitHub()


@pytest.fixture
def client(gh: FakeGitHub) -> GitHubClient:
    """A client of `gh`, on a clock that stands still (a test moves it on)."""
    return client_of(gh)


# ---------------------------------------------------------------------------------------------
# The calls: path, query, auth and body
# ---------------------------------------------------------------------------------------------


async def test_the_user_is_who_the_token_is(client: GitHubClient, gh: FakeGitHub) -> None:
    found = await client.user()

    assert (found.login, found.id) == ("udl-bot", 99)
    assert gh.calls == ["GET /user"]


async def test_every_request_says_which_token_and_which_udl(
    client: GitHubClient, gh: FakeGitHub
) -> None:
    await client.user()

    [sent] = gh.sent
    assert sent["authorization"] == f"Bearer {TOKEN}"
    assert sent["user-agent"] == user_agent()
    assert sent["accept"] == "application/vnd.github+json"
    assert sent["x-github-api-version"] == "2022-11-28"


async def test_a_repo_is_read_by_its_full_name(client: GitHubClient, gh: FakeGitHub) -> None:
    found = await client.get_repo("acme", "widgets")

    assert found == GhRepo.model_validate(gh.repos[REPO])
    assert gh.calls == ["GET /repos/acme/widgets"]


async def test_pulls_are_listed_by_state_and_head(client: GitHubClient, gh: FakeGitHub) -> None:
    found = await client.list_pulls(REPO, state="all", head="acme:feat/widgets", base="main")

    assert [pull.number for pull in found] == [7]
    assert gh.calls == [
        "GET /repos/acme/widgets/pulls?per_page=100&state=all&head=acme%3Afeat%2Fwidgets&base=main"
    ]


async def test_only_open_pulls_are_listed_by_default(client: GitHubClient) -> None:
    found = await client.list_pulls(REPO)

    assert [pull.number for pull in found] == [7, 8]
    assert found[0].head.sha == "abc1234"


async def test_one_pull_is_read_by_number(client: GitHubClient, gh: FakeGitHub) -> None:
    found = await client.get_pull(REPO, 8)

    assert (found.title, found.head.ref) == ("Fix the thing", "fix/thing")
    assert gh.calls == ["GET /repos/acme/widgets/pulls/8"]


async def test_a_pull_is_opened_with_its_body(client: GitHubClient, gh: FakeGitHub) -> None:
    found = await client.open_pull(
        REPO, head="feat/new", base="main", title="A new one", body="Why", draft=True
    )

    assert (found.number, found.title, found.draft) == (9, "A new one", True)
    assert gh.calls == ["POST /repos/acme/widgets/pulls"]
    assert gh.pull_of(REPO, 9).head == GhPull.Ref(ref="feat/new", sha="abc1234")


async def test_a_draft_is_only_sent_when_it_is_one(client: GitHubClient, gh: FakeGitHub) -> None:
    await client.open_pull(REPO, head="feat/new", base="main", title="A new one", body=None)

    assert gh.pull_of(REPO, 9).draft is False
    assert gh.pull_of(REPO, 9).body is None


async def test_a_pull_is_updated_by_number(client: GitHubClient, gh: FakeGitHub) -> None:
    found = await client.update_pull(REPO, 7, title="A better title")

    assert found.title == "A better title"
    assert found.body == "It adds a widget"  # (what was None is left as it was)
    assert gh.calls == ["PATCH /repos/acme/widgets/pulls/7"]


async def test_a_pull_is_merged_by_method(client: GitHubClient, gh: FakeGitHub) -> None:
    found = await client.merge_pull(REPO, 7, sha="abc1234", method="squash")

    assert found.merged is True
    assert found.message == "Pull Request successfully merged"
    assert gh.merged == ["acme/widgets#7"]
    assert gh.calls == ["PUT /repos/acme/widgets/pulls/7/merge"]


async def test_a_merge_of_another_commit_is_refused(client: GitHubClient) -> None:
    with pytest.raises(NotAllowed, match="Head branch was modified"):
        await client.merge_pull(REPO, 7, sha="other")


async def test_the_check_runs_of_a_commit_are_listed(client: GitHubClient, gh: FakeGitHub) -> None:
    gh.set_checks("abc1234", check_run_body("check", "success"))

    found = await client.list_check_runs(REPO, "abc1234")

    assert [run.name for run in found] == ["check"]
    assert gh.calls == ["GET /repos/acme/widgets/commits/abc1234/check-runs?per_page=100"]


async def test_the_comments_on_a_pull_are_listed(client: GitHubClient, gh: FakeGitHub) -> None:
    gh.comment("Looks good")

    found = await client.list_comments(REPO, 7)

    assert [note.body for note in found] == ["Looks good"]
    assert gh.calls == ["GET /repos/acme/widgets/issues/7/comments?per_page=100"]


async def test_a_comment_is_posted_as_the_token_s_user(
    client: GitHubClient, gh: FakeGitHub
) -> None:
    found = await client.add_comment(REPO, 7, "Running the checks")

    assert (found.body, found.user.login) == ("Running the checks", "udl-bot")
    assert gh.calls == ["POST /repos/acme/widgets/issues/7/comments"]


async def test_the_review_comments_of_a_pull_are_listed(
    client: GitHubClient, gh: FakeGitHub
) -> None:
    gh.review_comment("Rename this", line=42)

    [found] = await client.list_review_comments(REPO, 7)

    assert (found.body, found.path, found.line) == ("Rename this", "src/app.py", 42)
    assert found.in_reply_to_id is None
    assert gh.calls == ["GET /repos/acme/widgets/pulls/7/comments?per_page=100"]


async def test_failed_jobs_are_rerun_by_run_id(client: GitHubClient, gh: FakeGitHub) -> None:
    await client.rerun_failed_jobs(REPO, 7, 4242)

    assert gh.reruns == ["acme/widgets@4242"]
    assert gh.calls == ["POST /repos/acme/widgets/actions/runs/4242/rerun-failed-jobs"]


# ---------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------


async def test_a_pull_is_parsed_from_what_github_sends(client: GitHubClient) -> None:
    found = await client.get_pull(REPO, 7)

    assert found == GhPull(
        number=7,
        title="Add a widget",
        state=PullState.OPEN,
        draft=False,
        merged=False,
        mergeable=True,
        mergeable_state="clean",
        head=GhPull.Ref(ref="feat/widgets", sha="abc1234"),
        base=GhPull.Ref(ref="main", sha="def5678"),
        html_url="https://github.com/acme/widgets/pull/7",
        user=found.user,
        body="It adds a widget",
        updated_at=found.updated_at,
    )
    assert (found.user.login, found.user.id) == ("dev", 12)
    assert found.updated_at is not None
    assert found.updated_at.tzinfo is not None


async def test_what_github_says_nothing_about_is_none(client: GitHubClient, gh: FakeGitHub) -> None:
    gh.mangle = _without_a_mergeable

    found = await client.get_pull(REPO, 7)

    assert found.mergeable is None
    assert found.body is None


async def test_a_check_run_is_parsed_with_its_end(client: GitHubClient, gh: FakeGitHub) -> None:
    gh.set_checks("abc1234", check_run_body("check", None), check_run_body("lint", "failure"))

    [running, done] = await client.list_check_runs(REPO, "abc1234")

    assert (running.name, running.status, running.conclusion) == ("check", "in_progress", None)
    assert (done.name, done.status, done.conclusion) == ("lint", "completed", "failure")
    assert running.completed_at is None
    assert done.completed_at is not None


async def test_a_pull_that_is_not_the_model_is_refused(gh: FakeGitHub) -> None:
    gh.mangle = _without_head

    with pytest.raises(PluginError, match="GhPull") as raised:
        await client_of(gh).get_pull(REPO, 7)

    assert "udl's bug" in (raised.value.hint or "")


async def test_an_answer_that_is_not_the_model_at_all_is_refused() -> None:
    client = GitHubClient(
        base_url=BASE,
        token=SecretStr(TOKEN),
        http=api_of(lambda request: httpx.Response(200, json=[1, 2, 3])),
        clock=FakeClock(),
    )

    with pytest.raises(PluginError, match="GhPull"):
        await client.get_pull(REPO, 7)


async def test_a_body_that_is_not_json_is_refused() -> None:
    client = GitHubClient(
        base_url=BASE,
        token=SecretStr(TOKEN),
        http=api_of(lambda request: httpx.Response(200, text="<html>nope</html>")),
        clock=FakeClock(),
    )

    with pytest.raises(PluginError, match="not JSON"):
        await client.user()


async def test_a_merge_that_did_not_merge_is_refused() -> None:
    client = GitHubClient(
        base_url=BASE,
        token=SecretStr(TOKEN),
        http=api_of(
            lambda request: httpx.Response(
                200, json={"merged": False, "sha": "", "message": "Pull Request is not mergeable"}
            )
        ),
        clock=FakeClock(),
    )

    with pytest.raises(NotAllowed, match="not mergeable"):
        await client.merge_pull(REPO, 7, sha="abc1234")


def _without_head(body: Body) -> Body:
    """A body with its pull's head taken out (what GitHub always sends)."""
    if isinstance(body, dict):
        return {name: value for name, value in body.items() if name != "head"}
    return body


def _without_a_mergeable(body: Body) -> Body:
    """A body whose pull is not mergeable and has nothing to say (what GitHub sends while it
    works a pull out)."""
    if isinstance(body, dict):
        return {**body, "mergeable": None, "body": None}
    return body


# ---------------------------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------------------------


async def test_a_list_follows_the_link_header_to_its_last_page(gh: FakeGitHub) -> None:
    gh.page_size = 1

    found = await client_of(gh).list_pulls(REPO, state="all")

    assert [pull.number for pull in found] == [7, 8]
    assert len(gh.calls) == 2


async def test_a_list_that_fits_on_one_page_asks_once(client: GitHubClient, gh: FakeGitHub) -> None:
    gh.page_size = 10

    found = await client.list_pulls(REPO)

    assert len(found) == 2
    assert len(gh.calls) == 1


async def test_a_next_link_on_another_host_is_not_followed(gh: FakeGitHub) -> None:
    gh.page_size = 1
    gh.link_base = "https://evil.example"

    found = await client_of(gh).list_pulls(REPO)

    assert [pull.number for pull in found] == [7]
    assert len(gh.calls) == 1


# ---------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------


async def test_a_refused_token_is_reauthentication(gh: FakeGitHub) -> None:
    bad = GitHubClient(
        base_url=BASE, token=SecretStr("ghp_wrong"), http=gh.http(), clock=FakeClock()
    )

    with pytest.raises(ReauthRequired) as raised:
        await bad.user()

    assert "Bad credentials" in raised.value.message
    assert raised.value.hint is not None


async def test_a_secondary_rate_limit_says_how_long(client: GitHubClient, gh: FakeGitHub) -> None:
    gh.rate_limited = True

    with pytest.raises(RateLimited) as raised:
        await client.user()

    assert raised.value.retry_after == 12.0


async def test_another_403_is_not_allowed(client: GitHubClient, gh: FakeGitHub) -> None:
    gh.forbidden = True

    with pytest.raises(NotAllowed) as raised:
        await client.user()

    assert "would not allow" in raised.value.message


async def test_a_missing_repo_is_does_not_exist(client: GitHubClient) -> None:
    with pytest.raises(DoesNotExist) as raised:
        await client.get_repo("acme", "gone")

    assert "Not Found" in raised.value.message
    assert raised.value.hint is not None


@pytest.mark.parametrize("status", [405, 409, 422])
async def test_a_merge_github_refuses_is_not_allowed(
    client: GitHubClient, gh: FakeGitHub, status: int
) -> None:
    gh.fail = {"PUT /repos/acme/widgets/pulls/7/merge": status}

    with pytest.raises(NotAllowed) as raised:
        await client.merge_pull(REPO, 7, sha="abc1234")

    assert "would not do that" in raised.value.message


# ---------------------------------------------------------------------------------------------
# The retry policy
# ---------------------------------------------------------------------------------------------


async def test_a_server_error_is_retried_then_fails(gh: FakeGitHub) -> None:
    gh.fail = {"GET /user": 500}
    clock = FakeClock()

    with pytest.raises(PluginError) as raised:
        await moved(clock, client_of(gh, clock=clock).user())

    assert "500" in raised.value.message
    assert len(gh.calls) == GitHubClient.retries + 1


async def test_a_server_error_that_clears_is_not_raised() -> None:
    clock = FakeClock()
    tries = [0]

    def flaky(request: httpx.Request) -> httpx.Response:
        tries[0] += 1
        return httpx.Response(502 if tries[0] == 1 else 200, json={"login": "udl-bot", "id": 99})

    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=api_of(flaky), clock=clock)

    found = await moved(clock, client.user())

    assert (found.login, found.id) == ("udl-bot", 99)
    assert tries[0] == 2


async def test_a_retry_waits_longer_each_time_on_the_clock() -> None:
    clock = FakeClock()
    seen: list[datetime] = []

    def failing(request: httpx.Request) -> httpx.Response:
        seen.append(clock.now())
        return httpx.Response(503, json={"message": "unavailable"})

    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=api_of(failing), clock=clock)

    with pytest.raises(PluginError):
        await moved(clock, client.user())

    waits = [later - earlier for earlier, later in itertools.pairwise(seen)]
    assert waits == list(GitHubClient.backoff[: len(waits)])
    assert len(set(waits)) == len(waits)
    assert waits == sorted(waits)


async def test_a_connection_that_fails_is_retried_then_fails() -> None:
    clock = FakeClock()
    tries = [0]

    def refusing(request: httpx.Request) -> httpx.Response:
        tries[0] += 1
        raise httpx.ConnectError("refused")

    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=api_of(refusing), clock=clock)

    with pytest.raises(NetworkError):
        await moved(clock, client.user())

    assert tries[0] == GitHubClient.retries + 1


# ---------------------------------------------------------------------------------------------
# The API URL, and what the client keeps
# ---------------------------------------------------------------------------------------------


async def test_the_api_url_may_be_overridden_for_a_fake(gh: FakeGitHub) -> None:
    client = GitHubClient(
        base_url=f"{BASE}/",
        token=SecretStr(TOKEN),
        http=gh.http(),
        clock=FakeClock(),
    )

    await client.user()

    assert gh.calls == ["GET /user"]


async def test_the_client_remembers_the_rate_limit_github_reported(client: GitHubClient) -> None:
    assert client.rate_limit is None

    await client.user()

    found = client.rate_limit
    assert found is not None
    assert (found.limit, found.remaining) == (5000, 4998)
    assert found.reset > datetime.now(UTC)


async def test_a_client_without_a_token_sends_none(gh: FakeGitHub) -> None:
    gh.token = None

    found = await GitHubClient(base_url=BASE, token=None, http=gh.http(), clock=FakeClock()).user()

    assert found.login == "udl-bot"
    assert "authorization" not in gh.sent[0]


def test_the_token_is_never_printed() -> None:
    client = GitHubClient(
        base_url=BASE, token=SecretStr(TOKEN), http=FakeGitHub().http(), clock=FakeClock()
    )

    assert TOKEN not in repr(client)
    assert TOKEN not in str(client)


def test_the_default_api_url_is_githubs() -> None:
    assert DEFAULT_API_URL == "https://api.github.com"


def test_the_rate_limit_is_read_from_githubs_headers() -> None:
    limit = rate_limit_of(
        httpx.Response(
            200,
            headers={
                "X-RateLimit-Limit": "30",
                "X-RateLimit-Remaining": "29",
                "X-RateLimit-Reset": "1763377200",
            },
        )
    )

    assert limit is not None
    assert (limit.limit, limit.remaining) == (30, 29)
    assert limit.reset == datetime.fromtimestamp(1763377200, tz=UTC)


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-RateLimit-Limit": "30"}, dict(RATE, **{"X-RateLimit-Reset": "soon"})],
    ids=["none", "no-remaining", "a-nonsense-reset"],
)
def test_a_github_that_does_not_report_a_rate_limit_has_none(headers: dict[str, str]) -> None:
    assert rate_limit_of(httpx.Response(200, headers=headers)) is None


async def test_pulls_can_be_filtered_by_base(client: GitHubClient, gh: FakeGitHub) -> None:
    moved = gh.pull_of(REPO, 7).model_copy(update={"base": GhPull.Ref(ref="release", sha="111")})
    gh.pulls[REPO] = [moved]

    found = await client.list_pulls(REPO, base="release")

    assert [pull.number for pull in found] == [7]
    assert gh.calls == ["GET /repos/acme/widgets/pulls?per_page=100&state=open&base=release"]


async def test_a_list_that_never_ends_stops_at_its_cap(gh: FakeGitHub) -> None:
    gh.loop = True

    found = await client_of(gh).list_pulls(REPO, state="all")

    assert [pull.number for pull in found] == [7, 8]
    assert len(gh.calls) == GitHubClient.max_pages  # (the cap, not for ever)


async def test_a_list_github_answers_with_an_object_is_refused(gh: FakeGitHub) -> None:
    gh.mangle = lambda body: {"message": "no pulls for you"}

    with pytest.raises(PluginError, match="not a list"):
        await client_of(gh).list_pulls(REPO)


async def test_an_answer_with_nothing_in_it_is_none() -> None:
    client = GitHubClient(
        base_url=BASE,
        token=SecretStr(TOKEN),
        http=api_of(
            lambda request: httpx.Response(204, request=httpx.Request("POST", request.url))
        ),
        clock=FakeClock(),
    )

    assert await client.rerun_failed_jobs(REPO, 7, 1) is None


def test_the_client_can_be_built_for_an_integration() -> None:
    made = new_client(SecretStr(TOKEN), api_url="https://github.example/api/v3")

    assert made.base_url == "https://github.example/api/v3"
    assert made.token == SecretStr(TOKEN)


async def test_an_error_answer_without_a_message_says_the_status() -> None:
    client = GitHubClient(
        base_url=BASE,
        token=SecretStr(TOKEN),
        http=api_of(lambda request: httpx.Response(400, text="nope")),
        clock=FakeClock(),
    )

    with pytest.raises(NotAllowed, match="400"):
        await client.user()


async def test_a_page_whose_item_is_not_the_model_is_refused(gh: FakeGitHub) -> None:
    gh.mangle = _without_a_number

    with pytest.raises(PluginError, match="GhPull") as raised:
        await client_of(gh).list_pulls(REPO)

    assert "udl's bug" in (raised.value.hint or "")


async def test_a_retry_after_that_is_not_a_time_falls_back_to_the_window(gh: FakeGitHub) -> None:
    clock = FakeClock()
    gh.rate_limited = True
    gh.retry_after = "soon"
    gh.now = clock.now
    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=gh.http(), clock=clock)

    with pytest.raises(RateLimited) as raised:
        await client.user()

    # (The fake's window opens in 30 minutes, and that is what GitHub's headers say.)
    assert raised.value.retry_after == 1800.0


async def test_a_limit_that_says_nothing_at_all_falls_back_to_a_minute() -> None:
    client = GitHubClient(
        base_url=BASE,
        token=SecretStr(TOKEN),
        http=api_of(
            lambda request: httpx.Response(
                429, json={"message": "slow down"}, headers={"X-RateLimit-Remaining": "0"}
            )
        ),
        clock=FakeClock(),
    )

    with pytest.raises(RateLimited) as raised:
        await client.user()

    assert raised.value.retry_after == 60.0


async def test_a_retry_after_that_is_a_date_without_a_zone_is_still_a_date(
    gh: FakeGitHub,
) -> None:
    clock = FakeClock()
    gh.rate_limited = True
    gh.now = clock.now
    gh.retry_after = (clock.now() + timedelta(seconds=120)).strftime("%a, %d %b %Y %H:%M:%S")
    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=gh.http(), clock=clock)

    with pytest.raises(RateLimited) as raised:
        await client.user()

    assert raised.value.retry_after == 120.0


async def test_a_retry_after_that_is_a_date_with_a_zone_says_the_same(
    gh: FakeGitHub,
) -> None:
    clock = FakeClock()
    gh.rate_limited = True
    gh.retry_after = (clock.now() + timedelta(seconds=120)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=gh.http(), clock=clock)

    with pytest.raises(RateLimited) as raised:
        await client.user()

    assert raised.value.retry_after == 120.0


async def test_an_exhausted_limit_says_when_the_window_opens(gh: FakeGitHub) -> None:
    clock = FakeClock()
    gh.exhausted = True
    gh.now = clock.now
    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=gh.http(), clock=clock)

    with pytest.raises(RateLimited) as raised:
        await client.user()

    assert raised.value.retry_after == 1800.0
    assert raised.value.tool == "github"
    assert raised.value.to_info().retry_after == 1800.0


async def test_a_retry_after_header_beats_the_window(client: GitHubClient, gh: FakeGitHub) -> None:
    gh.exhausted = True
    gh.retry_after = "9"

    with pytest.raises(RateLimited) as raised:
        await client.user()

    assert raised.value.retry_after == 9.0


async def test_the_rate_limit_of_an_error_answer_is_kept(
    client: GitHubClient, gh: FakeGitHub
) -> None:
    gh.exhausted = True

    with pytest.raises(RateLimited):
        await client.user()

    assert client.rate_limit is not None
    assert client.rate_limit.remaining == 0


async def test_a_check_run_list_is_read_to_its_last_page(gh: FakeGitHub) -> None:
    runs = [check_run_body(f"check-{index}", "success") for index in range(120)]
    gh.set_checks("abc1234", *runs)

    found = await client_of(gh).list_check_runs(REPO, "abc1234")

    assert [run.name for run in found] == [run.name for run in runs]
    assert gh.calls[0] == "GET /repos/acme/widgets/commits/abc1234/check-runs?per_page=100"


async def test_check_runs_stop_at_the_page_cap_with_a_warning(
    gh: FakeGitHub, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(GitHubClient, "max_pages", 1)
    gh.page_size = 1
    gh.set_checks("abc1234", *[check_run_body(f"check-{index}", "success") for index in range(3)])

    found = await client_of(gh).list_check_runs(REPO, "abc1234")

    assert len(found) == 1
    assert "3 check runs, 1 read" in caplog.text


def test_whether_a_token_may_write_pulls() -> None:
    repo = gh_repo_body(REPO)

    assert repo.may_write_pulls is True

    readonly = repo.model_copy(
        update={"permissions": GhRepo.Permissions(admin=False, push=False, pull=True)}
    )
    assert readonly.permissions is not None
    assert readonly.permissions.writes_pulls is False
    assert readonly.may_write_pulls is False

    unknown = repo.model_copy(update={"permissions": None})
    assert unknown.may_write_pulls is False


def _without_a_number(body: Body) -> Body:
    """A page whose pulls have no number (what GitHub always sends)."""
    if isinstance(body, list):
        return [{name: value for name, value in item.items() if name != "number"} for item in body]
    return body


# ---------------------------------------------------------------------------------------------
# The review's items (17 Nov): what a watcher in 25.5 leans on
# ---------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------
# What is never tried twice, and what is
# ---------------------------------------------------------------------------------------------


async def test_a_comment_that_fails_on_a_5xx_is_not_posted_twice(gh: FakeGitHub) -> None:
    clock = FakeClock()
    gh.fail = {"POST /repos/acme/widgets/issues/7/comments": 502}
    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=gh.http(), clock=clock)

    with pytest.raises(PluginError) as raised:
        await client.add_comment(REPO, 7, "Running the checks")

    assert len(gh.calls) == 1
    assert "not tried again" in raised.value.message


async def test_a_pull_that_fails_on_a_5xx_is_not_opened_twice(gh: FakeGitHub) -> None:
    clock = FakeClock()
    gh.fail = {"POST /repos/acme/widgets/pulls": 502}
    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=gh.http(), clock=clock)

    with pytest.raises(PluginError):
        await client.open_pull(REPO, head="feat/x", base="main", title="X", body=None)

    assert len(gh.calls) == 1


async def test_a_merge_that_fails_on_a_5xx_is_not_tried_again(gh: FakeGitHub) -> None:
    clock = FakeClock()
    gh.fail = {"PUT /repos/acme/widgets/pulls/7/merge": 502}
    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=gh.http(), clock=clock)

    with pytest.raises(PluginError):
        await client.merge_pull(REPO, 7, sha="abc1234")

    assert len(gh.calls) == 1


async def test_a_read_that_fails_on_a_5xx_is_tried_again(gh: FakeGitHub) -> None:
    clock = FakeClock()
    gh.fail = {"GET /repos/acme/widgets": 502}
    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=gh.http(), clock=clock)

    with pytest.raises(PluginError):
        await moved(clock, client.get_repo("acme", "widgets"))

    assert len(gh.calls) == GitHubClient.retries + 1


async def test_an_error_that_is_not_retryable_is_raised_at_once(
    client: GitHubClient, gh: FakeGitHub
) -> None:
    gh.forbidden = True

    with pytest.raises(NotAllowed):
        await client.list_comments(REPO, 7)

    assert len(gh.calls) == 1


def test_a_merge_without_a_sha_does_not_send_one() -> None:
    gh = FakeGitHub()
    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=gh.http(), clock=FakeClock())

    import asyncio

    asyncio.run(client.merge_pull(REPO, 7, sha=None))

    assert "sha" not in gh.bodies[0]


# ---------------------------------------------------------------------------------------------
# The repository's name, and a token that is not there
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("owner", "name"),
    [
        ("acme", ""),
        ("", "widgets"),
        ("acme", "widgets/extra"),
        ("acme", ".."),
        ("acme", "wid get"),
        ("acme/../..", "user"),
    ],
    ids=["no-name", "no-owner", "too-far", "a-dot-dot", "a-space", "a-climb"],
)
async def test_a_repository_that_is_not_owner_name_is_refused(owner: str, name: str) -> None:
    client = GitHubClient(base_url=BASE, token=SecretStr(TOKEN), http=FakeGitHub().http())

    with pytest.raises(ConfigError, match="owner/name"):
        await client.get_repo(owner, name)


async def test_a_repository_without_a_token_is_read_from_what_github_says(
    gh: FakeGitHub,
) -> None:
    gh.token = None
    anon = GitHubClient(base_url=BASE, token=None, http=gh.http(), clock=FakeClock())

    repo = await anon.get_repo("acme", "widgets")

    assert repo.full_name == "acme/widgets"
    assert repo.permissions is None
    assert repo.may_write_pulls is False


async def test_an_error_that_is_not_retryable_is_not_retried_at_all() -> None:
    def gone(request: httpx.Request) -> httpx.Response:
        raise DoesNotExist("the pull request is gone")

    client = GitHubClient(
        base_url=BASE,
        token=SecretStr(TOKEN),
        http=api_of(gone),
        clock=FakeClock(),
    )

    with pytest.raises(DoesNotExist):
        await client.get_pull(REPO, 7)
