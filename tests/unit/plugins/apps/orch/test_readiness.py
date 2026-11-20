"""
The review bot's comments and what readiness reads of them (27.6).

The bot's comments in `tests/fixtures/reviews/` are real ones, copied from udl's pull requests:
a first review, a follow-up that asks for changes (an item NOT FIXED, a new issue and a nit), and
a follow-up that is all clear.
"""

# System Imports
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

# Package Imports
import pytest

# Local Imports
from udl.plugins.apps.orch.models import (
    Assessment,
    ReviewItem,
    ReviewStatus,
    Task,
)
from udl.plugins.apps.orch.readiness import (
    Check,
    Comment,
    Pull,
    clean,
    for_head,
    parse_review,
    readiness,
)

REVIEWS: Final = Path(__file__).parents[4] / "fixtures" / "reviews"
"""Where the bot's real comments are kept."""

BOT: Final = "claude[bot]"
"""The review bot's login, as udl's pulls see it."""

HEAD: Final = "dc1fd19aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
"""The commit at the head of the pull the tests are about."""

AT: Final = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
"""When the bot's comment was written (after the head's commit)."""


def _review(name: str) -> str:
    """The body of one of the bot's real comments."""
    return (REVIEWS / name).read_text()


def _pull(
    *,
    draft: bool = False,
    mergeable: bool | None = True,
    mergeable_state: str = "clean",
    checks: list[Check] | None = None,
    comments: list[Comment] | None = None,
    committed_at: datetime | None = None,
) -> Pull:
    """A pull that is ready, with whatever a test changes."""
    return Pull(
        draft=draft,
        mergeable=mergeable,
        mergeable_state=mergeable_state,
        head_sha=HEAD,
        head_committed_at=committed_at or datetime(2026, 11, 20, 9, 0, tzinfo=UTC),
        checks=[Check(name="check", status="completed", conclusion="success")]
        if checks is None
        else checks,
        comments=[Comment(author=BOT, body=_review("follow-up-all-clear.md"), created_at=AT)]
        if comments is None
        else comments,
    )


def _task(*, needs: list[str] | None = None, hold: str | None = None) -> Task:
    """The task whose pull it is."""
    return Task(
        project="udl",
        feature="F-1",
        key="F-1.1",
        title="It",
        needs=needs or [],
        hold=hold,
    )


# ---------------------------------------------------------------------------------------------
# parse_review: the bot's own words
# ---------------------------------------------------------------------------------------------


def test_a_follow_up_that_is_all_clear_is_clean() -> None:
    verdict = parse_review(_review("follow-up-all-clear.md"))
    assert verdict.assessment is Assessment.APPROVE
    assert verdict.items
    assert clean(verdict)
    assert {item.status for item in verdict.items} == {ReviewStatus.FIXED}


def test_a_follow_up_that_asks_for_changes_is_not_clean() -> None:
    verdict = parse_review(_review("follow-up-changes.md"))
    assert verdict.assessment is Assessment.REQUEST_CHANGES
    assert not clean(verdict)
    assert {item.status for item in verdict.items} >= {
        ReviewStatus.NOT_FIXED,
        ReviewStatus.NEW_ISSUE,
    }


def test_the_items_of_a_follow_up_are_the_earlier_ones_and_the_new_ones() -> None:
    verdict = parse_review(_review("follow-up-changes.md"))
    assert verdict.items[0].number == "1"
    assert verdict.items[0].text == "Actions never deleted"
    assert verdict.items[0].status is ReviewStatus.FIXED
    assert any(item.status is ReviewStatus.NEW_ISSUE for item in verdict.items)
    assert any(item.text == "Nit" for item in verdict.items)


def test_a_follow_up_names_the_commit_it_reviewed() -> None:
    assert parse_review(_review("follow-up-all-clear.md")).reviewed_sha == "dc1fd19"
    assert parse_review(_review("follow-up-changes.md")).reviewed_sha == "7a09481"


def test_a_first_review_names_no_commit() -> None:
    verdict = parse_review(_review("first-review.md"))
    assert verdict.reviewed_sha is None
    assert verdict.assessment is Assessment.REQUEST_CHANGES


def test_one_partially_fixed_row_is_not_clean_however_the_verdict_reads() -> None:
    body = """## Review: something (#1)

| # | Item | Status |
| - | ---- | ------ |
| 1 | The first thing | FIXED |
| 2 | The second thing | PARTIALLY FIXED |

**Verdict: all clear**
"""
    verdict = parse_review(body)
    assert [item.status for item in verdict.items] == [
        ReviewStatus.FIXED,
        ReviewStatus.PARTIALLY_FIXED,
    ]
    assert verdict.items[1].text == "The second thing"
    assert verdict.assessment is Assessment.APPROVE
    assert not clean(verdict)


def test_an_approve_line_never_overrides_an_open_item() -> None:
    body = """## Follow-up review: something (#1), after abc1234

### Earlier items
1. **The first thing: NOT FIXED.** It is still there.

**Verdict: all clear**
"""
    verdict = parse_review(body)
    assert verdict.assessment is Assessment.APPROVE
    assert not clean(verdict)


def test_a_status_udl_does_not_know_is_open() -> None:
    body = """| # | Item | Status |
| - | ---- | ------ |
| 1 | Something | LOOKS BETTER |
"""
    assert parse_review(body).items[0].status is ReviewStatus.OPEN


def test_a_comment_with_no_verdict_has_no_assessment() -> None:
    verdict = parse_review("## Review: something (#1)\n\nIt is fine, I think.\n")
    assert verdict.assessment is Assessment.UNKNOWN
    assert verdict.items == []


# ---------------------------------------------------------------------------------------------
# A comment whose table is not the first one, and a row that mentions a status word
# ---------------------------------------------------------------------------------------------


def test_a_table_below_a_row_that_says_status_is_not_read_again() -> None:
    """(27.6's review: `pr-status` in a row's text matched the word "status", so the rows below it
    were read a second time, doubling the items.)"""
    body = """## Follow-up review: it (#7), after dc1fd19

| # | Item | Status |
|---|------|--------|
| 1 | The `pr-status` line in a row | FIXED |
| 2 | Something else | FIXED |

| Task | What |
|------|------|
| 27.6 | Readiness |
"""

    verdict = parse_review(body)

    assert [(item.number, item.status) for item in verdict.items] == [
        ("1", ReviewStatus.FIXED),
        ("2", ReviewStatus.FIXED),
    ]


def test_the_table_is_the_first_one_the_comment_has() -> None:
    body = """## Review

| What | Why |
|------|-----|
| a | b |

| # | Item | Status |
|---|------|--------|
| 1 | It | OPEN |
"""

    verdict = parse_review(body)

    assert [(item.number, item.status) for item in verdict.items] == [("1", ReviewStatus.OPEN)]


# ---------------------------------------------------------------------------------------------
# for_head: the bot's comment must be about this head
# ---------------------------------------------------------------------------------------------


def test_a_follow_up_for_this_head_is_for_it() -> None:
    comment = Comment(author=BOT, body=_review("follow-up-all-clear.md"), created_at=AT)
    verdict = parse_review(comment.body)
    assert for_head(verdict, comment, _pull())


def test_a_follow_up_for_an_earlier_head_is_not_for_this_one() -> None:
    comment = Comment(author=BOT, body=_review("follow-up-changes.md"), created_at=AT)
    verdict = parse_review(comment.body)
    assert not for_head(verdict, comment, _pull())


def test_a_first_review_written_before_the_head_commit_is_not_for_it() -> None:
    comment = Comment(author=BOT, body=_review("first-review.md"), created_at=AT)
    verdict = parse_review(comment.body)
    assert not for_head(
        verdict, comment, _pull(committed_at=datetime(2026, 11, 20, 11, tzinfo=UTC))
    )
    assert for_head(verdict, comment, _pull(committed_at=datetime(2026, 11, 20, 9, tzinfo=UTC)))


def test_a_first_review_written_before_the_push_is_not_for_it() -> None:
    """(27.6's review: the commit's time is not the push's — a harness gates for minutes before
    it pushes, so a comment written in that window reviewed the head before this one.)"""
    written = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
    comment = Comment(author=BOT, body=_review("first-review.md"), created_at=written)
    verdict = parse_review(comment.body)
    # (Committed at 9:00, pushed at 9:55: a comment at 9:30 is for what was there before it.)
    pushed = _pull(
        committed_at=datetime(2026, 11, 20, 9, 0, tzinfo=UTC),
        checks=[
            Check(
                name="check",
                status="completed",
                conclusion="success",
                started_at=datetime(2026, 11, 20, 9, 55, tzinfo=UTC),
            )
        ],
    )
    assert not for_head(verdict, comment, pushed)

    # (With no check to say when it was pushed, the commit's own time stands: 9:30 is after it.)
    assert for_head(verdict, comment, _pull(committed_at=datetime(2026, 11, 20, 9, 0, tzinfo=UTC)))


def test_a_first_review_with_no_commit_time_is_not_for_it() -> None:
    comment = Comment(author=BOT, body=_review("first-review.md"), created_at=AT)
    verdict = parse_review(comment.body)
    pull = _pull()
    pull.head_committed_at = None
    assert not for_head(verdict, comment, pull)


# ---------------------------------------------------------------------------------------------
# readiness: what stops a merge
# ---------------------------------------------------------------------------------------------


def test_a_clean_pull_with_a_clean_review_is_ready() -> None:
    found = readiness(_pull(), bot=BOT, task=_task(), tasks={})
    assert found.ready
    assert found.blockers == []
    assert found.open_items == []
    assert not found.held


def test_a_draft_is_not_ready() -> None:
    found = readiness(_pull(draft=True), bot=BOT, task=_task(), tasks={})
    assert not found.ready
    assert found.blockers == ["it is a draft"]


def test_a_pull_github_has_not_worked_out_is_not_ready() -> None:
    found = readiness(_pull(mergeable=None), bot=BOT, task=_task(), tasks={})
    assert found.blockers == ["GitHub has not worked out whether it merges yet"]


def test_a_pull_that_does_not_merge_is_not_ready() -> None:
    found = readiness(
        _pull(mergeable=False, mergeable_state="dirty"), bot=BOT, task=_task(), tasks={}
    )
    assert found.blockers == ["it does not merge cleanly (dirty)"]


def test_a_pull_that_merges_but_is_not_clean_is_not_ready() -> None:
    found = readiness(_pull(mergeable_state="behind"), bot=BOT, task=_task(), tasks={})
    assert found.blockers == ["GitHub says it is behind, not clean"]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "action_required"])
def test_a_failed_check_is_not_ready(conclusion: str) -> None:
    checks = [Check(name="check", status="completed", conclusion=conclusion)]
    found = readiness(_pull(checks=checks), bot=BOT, task=_task(), tasks={})
    assert found.blockers == ["its checks failed: check"]


def test_only_the_blockers_a_wait_can_change_are_pending() -> None:
    """(27.5's wait holds while everything in the way is a matter of time, and a failed check is
    not: it will not pass by waiting.)"""
    running = [Check(name="check", status="in_progress", conclusion=None)]
    found = readiness(_pull(checks=running), bot=BOT, task=_task(), tasks={})

    assert found.blockers == ["its checks are still running: check"]
    assert found.pending == found.blockers

    failed = [Check(name="check", status="completed", conclusion="failure")]
    other = readiness(_pull(checks=failed), bot=BOT, task=_task(), tasks={})

    assert other.blockers == ["its checks failed: check"]
    assert other.pending == []


def test_a_draft_is_never_pending() -> None:
    found = readiness(_pull(draft=True), bot=BOT, task=_task(), tasks={})
    assert found.pending == []


@pytest.mark.parametrize("conclusion", ["success", "neutral", "skipped"])
def test_a_check_that_passed_is_ready(conclusion: str) -> None:
    checks = [Check(name="check", status="completed", conclusion=conclusion)]
    assert readiness(_pull(checks=checks), bot=BOT, task=_task(), tasks={}).ready


def test_a_check_still_running_is_not_ready() -> None:
    checks = [Check(name="check", status="in_progress")]
    found = readiness(_pull(checks=checks), bot=BOT, task=_task(), tasks={})
    assert found.blockers == ["its checks are still running: check"]


def test_a_pull_the_bot_has_not_reviewed_is_not_ready() -> None:
    found = readiness(_pull(comments=[]), bot=BOT, task=_task(), tasks={})
    assert found.blockers == [f"the review bot ({BOT}) has not commented on it"]


def test_a_bot_comment_for_an_earlier_head_is_not_ready() -> None:
    comments = [
        Comment(author=BOT, body=_review("follow-up-changes.md"), created_at=AT),
    ]
    found = readiness(_pull(comments=comments), bot=BOT, task=_task(), tasks={})
    assert found.blockers == ["the review bot's latest comment is not about dc1fd19"]
    assert found.open_items == []


def test_the_bots_open_items_are_reported_with_the_blocker() -> None:
    comments = [Comment(author=BOT, body=_review("follow-up-changes.md"), created_at=AT)]
    pull = _pull(comments=comments)
    pull.head_sha = "7a09481bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    found = readiness(pull, bot=BOT, task=_task(), tasks={})
    assert not found.ready
    assert found.blockers == [
        "the review bot asks for changes",
        "the review bot lists 3 open item(s)",
    ]
    assert [item.status for item in found.open_items] == [
        ReviewStatus.NOT_FIXED,
        ReviewStatus.NEW_ISSUE,
        ReviewStatus.OPEN,
    ]
    assert found.open_items[-1].text == "Nit"


def test_the_bots_latest_comment_is_the_one_that_counts() -> None:
    earlier = Comment(author=BOT, body=_review("follow-up-changes.md"), created_at=AT)
    later = Comment(
        author=BOT,
        body=_review("follow-up-all-clear.md"),
        created_at=datetime(2026, 11, 20, 11, tzinfo=UTC),
    )
    found = readiness(_pull(comments=[earlier, later]), bot=BOT, task=_task(), tasks={})
    assert found.ready


def test_a_comment_by_someone_else_is_not_the_bots() -> None:
    comments = [Comment(author="yelrom0", body="LGTM", created_at=AT)]
    found = readiness(_pull(comments=comments), bot=BOT, task=_task(), tasks={})
    assert found.blockers == [f"the review bot ({BOT}) has not commented on it"]


def test_a_pull_with_no_bot_to_wait_for_is_ready() -> None:
    found = readiness(_pull(comments=[]), bot=None, task=_task(), tasks={})
    assert found.ready


def test_a_task_that_waits_on_an_unmerged_one_is_not_ready() -> None:
    found = readiness(_pull(), bot=BOT, task=_task(needs=["26.2"]), tasks={"26.2": "ready"})
    assert found.blockers == ["it waits on 26.2, which is ready"]


def test_a_task_that_waits_on_a_merged_one_is_ready() -> None:
    assert readiness(_pull(), bot=BOT, task=_task(needs=["26.2"]), tasks={"26.2": "merged"}).ready
    assert readiness(_pull(), bot=BOT, task=_task(needs=["26.2"]), tasks={"26.2": "done"}).ready


def test_a_task_that_waits_on_one_that_is_not_there_is_not_ready() -> None:
    found = readiness(_pull(), bot=BOT, task=_task(needs=["26.2"]), tasks={})
    assert found.blockers == ["it waits on 26.2, which is not a task"]


def test_a_held_task_is_reported_as_held_not_ready() -> None:
    found = readiness(_pull(), bot=BOT, task=_task(hold="build now, merge after 27.1"), tasks={})
    assert not found.ready
    assert found.held
    assert found.blockers == ["it is on hold: build now, merge after 27.1"]


def test_a_held_task_that_is_also_not_clean_is_not_held_but_blocked() -> None:
    found = readiness(_pull(draft=True), bot=BOT, task=_task(hold="merge after 27.1"), tasks={})
    assert not found.ready
    assert not found.held
    assert found.blockers == ["it is a draft", "it is on hold: merge after 27.1"]


def test_the_blockers_are_in_the_order_a_person_would_look_at_them() -> None:
    checks = [
        Check(name="check", status="completed", conclusion="failure"),
        Check(name="e2e", status="queued"),
    ]
    found = readiness(
        _pull(draft=True, mergeable=False, mergeable_state="dirty", checks=checks),
        bot=BOT,
        task=_task(needs=["26.2"], hold="later"),
        tasks={"26.2": "queued"},
    )
    assert found.blockers == [
        "it is a draft",
        "it does not merge cleanly (dirty)",
        "its checks are still running: e2e",
        "its checks failed: check",
        "it waits on 26.2, which is queued",
        "it is on hold: later",
    ]


def test_a_review_item_with_no_status_reads_as_open() -> None:
    assert ReviewItem(text="Something").status is ReviewStatus.OPEN
