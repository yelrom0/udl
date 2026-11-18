"""
------------------
- test_plugin.py -
------------------

The github integration: who a token is (and that it may write each of the account's
repositories), what checking it says, each operation against the fake GitHub (the repositories
an account names, and only those), merging being the person's own (no MCP tool, and a confirm
that says which pull), and the watcher: what happens to a pull, each published once, reading a
repository again only when GitHub says it changed
"""

# System Imports
import asyncio
from collections.abc import Callable
from datetime import timedelta

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.clock import FakeClock
from support.github import BASE, LOGIN, TOKEN, FakeGitHub, check_run_body
from support.integrations import MemoryState, RecordingSink, StubHandle
from udl.errors import ConfigError, NotAllowed, ReauthRequired
from udl.plugins.integrations.base import Account, AccountStatus, ConnectMethod
from udl.plugins.integrations.github.models import GhCheckRun
from udl.plugins.integrations.github.plugin import (
    CommentRequest,
    GetPullRequest,
    GitHubIntegration,
    GitHubOptions,
    GitHubSettings,
    GitHubState,
    GitHubToken,
    ListPullsRequest,
    MergePullRequest,
    OpenPullRequest,
    RerunRequest,
)

REPO = "acme/widgets"


@pytest.fixture
def fake() -> FakeGitHub:
    """A GitHub with a token, a repository and two open pulls."""
    return FakeGitHub()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def github(fake: FakeGitHub, clock: FakeClock) -> GitHubIntegration:
    """The integration, on the fake and a clock that stands still."""
    return GitHubIntegration(GitHubSettings(), http=fake.http(), clock=clock)


def options(**given: object) -> GitHubOptions:
    """An account's options: the fake's repository, and its API."""
    return GitHubOptions(repos=[REPO], api_url=BASE, **given)


def handle(
    found: GitHubOptions | None = None, *, token: str = TOKEN, refused: bool = False
) -> StubHandle[GitHubOptions]:
    """An account of the fake, connected with `token`."""
    account = Account(
        integration="github",
        name="work",
        method=ConnectMethod.TOKENS,
        status=AccountStatus.CONNECTED,
    )
    credentials = GitHubToken(token=SecretStr(token))
    return StubHandle(account, found or options(), credentials, refused=refused)


def failing_run(name: str = "pytest", run: int = 555) -> GhCheckRun:
    """A check run that failed, of the Actions run `run`."""
    return GhCheckRun(
        name=name,
        status="completed",
        conclusion="failure",
        html_url=f"https://github.com/acme/widgets/actions/runs/{run}",
    )


# ---------------------------------------------------------------------------------------------
# Who an account is
# ---------------------------------------------------------------------------------------------


async def test_identify_says_whose_token_it_is(github: GitHubIntegration) -> None:
    found = await github.identify(GitHubToken(token=SecretStr(TOKEN)), options())

    assert found.id == "99"
    assert found.display == f"{LOGIN} ({REPO})"


async def test_an_account_must_name_its_repositories(github: GitHubIntegration) -> None:
    with pytest.raises(ConfigError, match="names the repositories"):
        await github.identify(GitHubToken(token=SecretStr(TOKEN)), GitHubOptions(api_url=BASE))


async def test_a_token_that_may_not_write_is_refused(
    github: GitHubIntegration, fake: FakeGitHub
) -> None:
    found = fake.repos[REPO]
    assert found.permissions is not None
    fake.repos[REPO] = found.model_copy(
        update={"permissions": found.permissions.model_copy(update={"push": False})}
    )

    with pytest.raises(NotAllowed, match="cannot work in every one"):
        await github.identify(GitHubToken(token=SecretStr(TOKEN)), options())


async def test_a_repository_it_cannot_see_is_refused(github: GitHubIntegration) -> None:
    with pytest.raises(NotAllowed, match="cannot work in"):
        await github.identify(
            GitHubToken(token=SecretStr(TOKEN)),
            GitHubOptions(repos=["acme/elsewhere"], api_url=BASE),
        )


async def test_a_connected_account_is_healthy(github: GitHubIntegration) -> None:
    health = await github.check(handle())

    assert health.ok
    assert health.problems == []


async def test_checking_says_what_is_broken(github: GitHubIntegration, fake: FakeGitHub) -> None:
    del fake.repos[REPO]

    health = await github.check(handle())

    assert health.ok is False
    assert health.problems
    assert "acme/widgets" in health.problems[0]


# ---------------------------------------------------------------------------------------------
# The operations
# ---------------------------------------------------------------------------------------------


def test_merging_is_no_tool_for_an_agent() -> None:
    specs = {spec.name: spec for spec in GitHubIntegration.operation_specs()}

    assert specs["merge_pull"].hints.mcp is False
    assert specs["merge_pull"].hints.destructive
    assert all(spec.hints.mcp for name, spec in specs.items() if name != "merge_pull")


async def test_the_pulls_are_listed_a_page_at_a_time(github: GitHubIntegration) -> None:
    page = await github.list_pulls(handle(), ListPullsRequest(repo=REPO, limit=1))

    assert [pull.number for pull in page.items] == [7]
    assert page.next_page == "1"

    rest = await github.list_pulls(handle(), ListPullsRequest(repo=REPO, page="1", limit=1))

    assert [pull.number for pull in rest.items] == [8]
    assert rest.next_page is None


async def test_a_pull_is_read_with_its_checks_and_comments(
    github: GitHubIntegration, fake: FakeGitHub
) -> None:
    fake.set_checks("abc1234", check_run_body("pytest", "failure"))
    fake.comment_as("Please fix the test")
    fake.review_comment("This line is wrong", path="src/app.py", line=42)

    found = await github.get_pull(handle(), GetPullRequest(repo=REPO, number=7))

    assert (found.number, found.state, found.head, found.base) == (
        7,
        "open",
        "feat/widgets",
        "main",
    )
    assert found.mergeable is True
    assert [(check.name, check.conclusion) for check in found.checks] == [("pytest", "failure")]
    assert [(note.author, note.body) for note in found.comments] == [
        ("human", "Please fix the test")
    ]
    assert [(note.path, note.line) for note in found.review_comments] == [("src/app.py", 42)]


async def test_a_repository_the_account_does_not_name_is_refused(
    github: GitHubIntegration, fake: FakeGitHub
) -> None:
    fake.repos["acme/elsewhere"] = fake.repos[REPO].model_copy(
        update={"full_name": "acme/elsewhere"}
    )

    with pytest.raises(NotAllowed, match="not one of this github account's"):
        await github.get_pull(handle(), GetPullRequest(repo="acme/elsewhere", number=7))


async def test_a_pull_is_opened_into_the_default_branch(
    github: GitHubIntegration, fake: FakeGitHub
) -> None:
    found = await github.open_pull(
        handle(), OpenPullRequest(repo=REPO, head="feat/new", title="A new one", body="Why")
    )

    assert (found.number, found.title, found.base) == (9, "A new one", "main")
    assert fake.pull_of(REPO, 9).head.ref == "feat/new"


async def test_a_pull_already_open_for_that_head_is_updated(
    github: GitHubIntegration, fake: FakeGitHub
) -> None:
    found = await github.open_pull(
        handle(), OpenPullRequest(repo=REPO, head="feat/widgets", title="A better title")
    )

    assert found.number == 7
    assert found.title == "A better title"
    assert len(fake.pulls[REPO]) == 2  # (no second pull for the same head)


async def test_a_pull_is_commented_on(github: GitHubIntegration, fake: FakeGitHub) -> None:
    found = await github.comment(handle(), CommentRequest(repo=REPO, number=7, body="Looks good"))

    assert (found.author, found.body) == (LOGIN, "Looks good")
    assert [note.body for note in fake.comments["acme/widgets#7"]] == ["Looks good"]


async def test_a_pulls_failed_checks_are_run_again(
    github: GitHubIntegration, fake: FakeGitHub
) -> None:
    fake.set_checks("abc1234", failing_run("pytest", 555), failing_run("ruff", 556))

    found = await github.rerun_checks(handle(), RerunRequest(repo=REPO, number=7))

    assert found.runs == [555, 556]
    assert sorted(found.checks) == ["pytest", "ruff"]
    assert sorted(fake.reruns) == ["acme/widgets@555", "acme/widgets@556"]


async def test_a_pull_with_no_failed_check_has_nothing_to_run_again(
    github: GitHubIntegration, fake: FakeGitHub
) -> None:
    fake.set_checks("abc1234", check_run_body("pytest", "success"))

    with pytest.raises(NotAllowed, match="no failed check"):
        await github.rerun_checks(handle(), RerunRequest(repo=REPO, number=7))


async def test_a_merge_says_which_pull_it_is(github: GitHubIntegration) -> None:
    with pytest.raises(ConfigError, match="confirm=acme/widgets#7"):
        await github.merge_pull(
            handle(),
            MergePullRequest(repo=REPO, number=7, sha="abc1234", confirm="acme/widgets#8"),
        )


async def test_a_pull_is_merged_by_its_reviewed_commit(
    github: GitHubIntegration, fake: FakeGitHub
) -> None:
    found = await github.merge_pull(
        handle(),
        MergePullRequest(repo=REPO, number=7, sha="abc1234", confirm="acme/widgets#7"),
    )

    assert found.merged is True
    assert fake.merged == ["acme/widgets#7"]


async def test_a_merge_of_another_commit_merges_nothing(
    github: GitHubIntegration, fake: FakeGitHub
) -> None:
    with pytest.raises(NotAllowed, match="Head branch was modified"):
        await github.merge_pull(
            handle(),
            MergePullRequest(repo=REPO, number=7, sha="other", confirm="acme/widgets#7"),
        )

    assert fake.merged == []


# ---------------------------------------------------------------------------------------------
# The watcher
# ---------------------------------------------------------------------------------------------


async def until(check: Callable[[], bool], clock: FakeClock) -> None:
    """Move the clock on, a second at a time, until `check()` (or fail after 5 minutes)."""
    for _ in range(300):
        if check():
            return
        await clock.advance(timedelta(seconds=1))
    raise AssertionError("it never happened")


def watching(
    github: GitHubIntegration,
    account: StubHandle[GitHubOptions],
    sink: RecordingSink,
    state: MemoryState,
) -> asyncio.Task[None]:
    """The integration's watcher, running until the test cancels it."""
    return asyncio.create_task(github.watch(account, sink, state))


async def test_the_watcher_publishes_what_happens_to_a_pull(
    github: GitHubIntegration, fake: FakeGitHub, clock: FakeClock
) -> None:
    sink = RecordingSink(GitHubIntegration)
    state = MemoryState(GitHubIntegration.state_model)
    task = watching(github, handle(), sink, state)
    try:
        await until(lambda: len(sink.emitted) >= 2, clock)  # (the fake's two pulls)

        fake.push("def5678", number=7)
        fake.set_checks("def5678", check_run_body("pytest", None))
        await until(lambda: any(e.event_type == "github.pr.updated" for e in sink.emitted), clock)

        fake.finish_check("def5678", "failure")
        await until(
            lambda: any(e.event_type == "github.pr.checks_finished" for e in sink.emitted), clock
        )

        fake.comment_as("Please fix the test")
        await until(lambda: any(e.event_type == "github.pr.commented" for e in sink.emitted), clock)

        fake.merge(number=7)
        await until(lambda: any(e.event_type == "github.pr.merged" for e in sink.emitted), clock)
    finally:
        task.cancel()
        await asyncio.wait([task])

    kinds = [emitted.event_type for emitted in sink.emitted]
    assert kinds.count("github.pr.opened") == 2
    assert kinds.count("github.pr.updated") == 1
    assert kinds.count("github.pr.checks_finished") == 1
    assert kinds.count("github.pr.commented") == 1
    assert kinds.count("github.pr.merged") == 1
    checks = next(e for e in sink.emitted if e.event_type == "github.pr.checks_finished")
    assert checks.data["conclusion"] == "failure"
    assert checks.data["failed"] == ["pytest"]
    comment = next(e for e in sink.emitted if e.event_type == "github.pr.commented")
    assert (comment.data["author"], comment.data["is_bot"]) == ("human", False)
    assert len({emitted.dedupe_key for emitted in sink.emitted}) == len(sink.emitted)


async def test_a_repository_is_read_again_only_when_it_changed(
    github: GitHubIntegration, fake: FakeGitHub, clock: FakeClock
) -> None:
    sink = RecordingSink(GitHubIntegration)
    state = MemoryState(GitHubIntegration.state_model)
    task = watching(github, handle(), sink, state)
    try:
        await until(lambda: len(sink.emitted) >= 2, clock)
        looks = len(fake.calls)

        await clock.advance(timedelta(seconds=180))

        # (Three more looks: the pulls are a 304, and each open pull's checks are asked about
        # again — they never started — so nothing is published.)
        assert len(fake.calls) == looks + 3 * 3
        assert len(sink.emitted) == 2
    finally:
        task.cancel()
        await asyncio.wait([task])


async def test_the_watcher_goes_on_from_where_it_was(
    github: GitHubIntegration, fake: FakeGitHub, clock: FakeClock
) -> None:
    state = MemoryState(GitHubIntegration.state_model)
    first = RecordingSink(GitHubIntegration)
    task = watching(github, handle(), first, state)
    await until(lambda: len(first.emitted) >= 2, clock)
    task.cancel()
    await asyncio.wait([task])

    fake.open_pull(number=9, head="feat/another")
    again = RecordingSink(GitHubIntegration)
    task = watching(github, handle(), again, state)
    try:
        await until(lambda: len(again.emitted) >= 1, clock)
        await clock.advance(timedelta(seconds=180))
    finally:
        task.cancel()
        await asyncio.wait([task])

    assert [emitted.event_type for emitted in again.emitted] == ["github.pr.opened"]
    assert again.emitted[0].data["number"] == 9


async def test_the_watcher_stops_when_the_credentials_are_refused(
    github: GitHubIntegration, clock: FakeClock
) -> None:
    sink = RecordingSink(GitHubIntegration)
    state = MemoryState(GitHubIntegration.state_model)

    with pytest.raises(ReauthRequired, match="credentials were refused"):
        async with asyncio.timeout(1):
            await github.watch(handle(refused=True), sink, state)


async def test_its_state_is_the_etags_and_the_pulls_it_saw(
    github: GitHubIntegration, fake: FakeGitHub, clock: FakeClock
) -> None:
    state = MemoryState(GitHubIntegration.state_model)
    task = watching(github, handle(), RecordingSink(GitHubIntegration), state)
    await until(lambda: bool(state.saved), clock)
    task.cancel()
    await asyncio.wait([task])

    saved = state.saved[-1]
    assert isinstance(saved, GitHubState)
    assert set(saved.etags) == {REPO}
    assert set(saved.pulls) == {"acme/widgets#7", "acme/widgets#8"}
    assert saved.pulls["acme/widgets#7"].head_sha == "abc1234"
