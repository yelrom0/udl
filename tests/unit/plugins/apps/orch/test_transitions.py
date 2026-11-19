"""
---------------------------------
- test_transitions.py -
---------------------------------

Task states and their transitions (udl.plugins.apps.orch.transitions): the phase's first
bullet, generated from the table itself plus the refusals — every (state, operation, caller
role) triple is either allowed or refused by one table-driven test; only a `reviewer` agent or a
human reaches `done`; and an implementer can't change a task that isn't its own.
"""

# System Imports

# Package Imports
import pytest

# Local Imports
from udl.errors import NotAllowed
from udl.plugins.apps.orch.models import (
    BLOCKED,
    DONE,
    MERGED,
    OPEN,
    QUEUED,
    READY,
    ROLE,
    STATE,
    STATE_MARK,
    Principal,
    Role,
)
from udl.plugins.apps.orch.records import Task as TaskRecord
from udl.plugins.apps.orch.transitions import TRANSITIONS, allowed, move, roles

# ---------------------------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------------------------


def test_the_states_are_the_concepts_table_s() -> None:
    assert set(STATE) == {
        "open",
        "queued",
        "in_progress",
        "blocked",
        "ready",
        "merged",
        "done",
        "moved",
        "cancelled",
    }
    assert (OPEN, READY, MERGED, DONE) == ("open", "ready", "merged", "done")


def test_each_state_has_the_mark_the_concepts_table_gives_it() -> None:
    assert STATE_MARK == {
        OPEN: "[ ]",
        "queued": "[ ]",
        "in_progress": "[ ]",
        BLOCKED: "[!]",
        READY: "[r]",
        MERGED: "[~]",
        DONE: "[x]",
        "moved": "[m]",
        "cancelled": "\u2014",
    }


def test_the_operations_are_the_cards() -> None:
    assert set(ROLE) == {"assign", "start", "block", "ready", "merge", "review", "move", "cancel"}


def test_every_transition_names_states_and_roles_that_exist() -> None:
    for (from_state, to_state), may in TRANSITIONS.items():
        assert from_state in STATE, from_state
        assert to_state in STATE, to_state
        assert may, (from_state, to_state)


@pytest.mark.parametrize(
    ("from_state", "to_state", "role"),
    [(from_state, to_state, role) for from_state in STATE for to_state in STATE for role in ROLE],
)
def test_every_state_operation_and_role_is_allowed_or_refused(
    from_state: str, to_state: str, role: str
) -> None:
    # (Every pair of states, not only the rows the table has: the pairs it does not have are
    # refused too. The task is the caller's own, so what is tested is the table and the rules,
    # not who owns it — that is the tests under "Who may do what".)
    caller = who(role)
    task = task_in(from_state, assignee=caller.who)
    allowed = from_state != to_state and (people(caller) or role in roles(from_state, to_state))
    if allowed:
        moved = move(task, to_state, by=caller)
        assert (moved.state, moved.updated_by) == (to_state, caller.who)
    else:
        with pytest.raises(NotAllowed) as refused:
            move(task, to_state, by=caller)
        assert refused.value.hint


def test_the_table_answers_what_a_role_may_do() -> None:
    assert roles("in_progress", READY) == ("ready", "human")
    assert roles("in_progress", "merged") == ()
    assert allowed("in_progress", READY, "ready") is True
    assert allowed("in_progress", READY, "merge") is False


# ---------------------------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------------------------


def test_only_a_reviewer_or_a_human_reaches_done() -> None:
    task = task_in(MERGED)

    assert move(task, DONE, by=who("review")).state == DONE
    assert move(task, DONE, by=Principal(kind="agent", name="reviewer")).state == DONE
    assert move(task, DONE, by=who("human")).state == DONE
    for role in ROLE:
        if role in ("review", "human"):
            continue
        with pytest.raises(NotAllowed, match=r"reviewer or a human"):
            move(task, DONE, by=who("agent", name=role))


def test_a_move_with_no_role_of_its_own_says_which_verb_it_is() -> None:
    # (A caller that does not say what it is doing is the verb its move is, when the table has
    # one: a start, a merge, a cancel, a move, an assign — or nothing at all.)
    mine = {"assignee": "agent"}
    assert move(task_in(OPEN, **mine), "in_progress", by=who("agent")).state == "in_progress"
    assert move(task_in(OPEN, **mine), QUEUED, by=who("agent")).state == QUEUED
    assert move(task_in("in_progress", **mine), QUEUED, by=who("agent")).state == QUEUED
    assert move(task_in(BLOCKED, **mine), "in_progress", by=who("agent")).state == "in_progress"
    assert move(task_in("in_progress", **mine), READY, by=who("agent")).state == READY
    assert move(task_in("in_progress", **mine), BLOCKED, by=who("agent")).state == BLOCKED
    assert move(task_in("in_progress", **mine), "cancelled", by=who("agent")).state == "cancelled"
    assert move(task_in(READY, **mine), MERGED, by=who("agent")).state == MERGED
    assert move(task_in(QUEUED, **mine), "moved", by=who("agent")).state == "moved"
    with pytest.raises(NotAllowed) as refused:
        move(task_in("moved", **mine), MERGED, by=who("agent"))
    assert "No role moves a task from 'moved' to 'merged'" in (refused.value.hint or "")


def test_a_human_is_a_caller_in_its_own_right() -> None:
    assert move(task_in(OPEN), READY, by=who("human")).state == READY
    assert move(task_in(OPEN), DONE, by=who("human")).state == DONE


def test_an_agent_may_not_claim_to_be_a_person_by_its_name() -> None:
    # (A person is a person by its kind: an agent called `human:claude` is held to the agents'
    # rules, so it can neither move another agent's task nor mark one done.)
    spoof = Principal(kind="agent", name="human:claude", role="ready")
    with pytest.raises(NotAllowed, match=r"not yours"):
        move(task_in("in_progress", assignee="ernie-c"), READY, by=spoof)
    with pytest.raises(NotAllowed, match=r"reviewer or a human"):
        move(task_in(MERGED), DONE, by=Principal(kind="agent", name="human:claude", role="review"))


def test_udl_itself_moves_a_task_that_is_not_its_own() -> None:
    # (The dispatcher assigns an open task, which is nobody's, and the watcher merges a ready
    # one, which is the implementer's: both are udl, and stay bound by the table.)
    udl = Principal(kind="system")

    assert move(task_in(OPEN), QUEUED, by=udl).state == QUEUED
    assert move(task_in(READY, assignee="ernie-c"), MERGED, by=udl).state == MERGED
    with pytest.raises(NotAllowed, match=r"from 'open' to 'merged'"):
        move(task_in(OPEN), MERGED, by=udl)


def test_an_implementer_saying_assign_is_still_refused_another_agents_task() -> None:
    with pytest.raises(NotAllowed, match=r"not yours"):
        move(task_in(OPEN), QUEUED, by=Principal(kind="agent", name="ernie-c", role="assign"))


def test_a_role_does_not_make_another_agents_task_yours() -> None:
    # (A caller says what it is doing; it does not say what it may touch.)
    task = task_in("in_progress", assignee="ernie-b")

    for role in ("cancel", "move", "assign"):
        with pytest.raises(NotAllowed, match=r"not yours"):
            move(task, "cancelled", by=Principal(kind="agent", name="ernie-c", role=role))


def test_an_implementer_can_move_its_own_task() -> None:
    task = task_in("in_progress", assignee="ernie-c")

    assert move(task, READY, by=who("ready", name="ernie-c")).state == READY


def test_an_implementer_cannot_move_another_agents_task() -> None:
    task = task_in("in_progress", assignee="ernie-b")

    with pytest.raises(NotAllowed, match=r"not yours"):
        move(task, READY, by=who("ready", name="ernie-c"))


def test_a_task_with_no_assignee_is_nobodys_to_move() -> None:
    with pytest.raises(NotAllowed, match=r"not yours"):
        move(task_in("queued"), "in_progress", by=who("agent", name="ernie-c"))


def test_a_human_may_move_any_task() -> None:
    task = task_in("in_progress", assignee="ernie-b")

    assert move(task, BLOCKED, by=who("human"), note="a question").blocked_on == "a question"


def test_a_blocked_task_says_what_it_waits_on() -> None:
    task = move(
        task_in("in_progress"),
        BLOCKED,
        by=who("human"),
        note="Q-7: which key?",
    )

    assert (task.state, task.blocked_on) == (BLOCKED, "Q-7: which key?")


def test_moving_on_clears_what_was_blocking() -> None:
    task = task_in(BLOCKED, blocked_on="Q-7: which key?")
    moved = move(task, "in_progress", by=who("human"))

    assert (moved.state, moved.blocked_on) == ("in_progress", None)


def test_a_cancelled_task_keeps_its_hold() -> None:
    # (A hold is why a task is held — built now, merged later — not why it was cancelled: the
    # reason for a cancel or a move is a note (26.5), and a move does not lose the hold.)
    task = move(
        task_in("in_progress", hold="merge after #12"), "cancelled", by=who("human"), note="x"
    )

    assert (task.state, task.hold) == ("cancelled", "merge after #12")


def test_a_moved_task_says_who_took_it() -> None:
    task = task_in("queued", assignee="ernie-c")

    moved = move(task, "moved", by=who("human"))

    assert (moved.state, moved.assignee, moved.updated_by) == ("moved", None, "human:claude")


def test_moving_a_task_to_its_own_state_is_refused() -> None:
    task = task_in("in_progress", assignee="ernie-c")

    with pytest.raises(NotAllowed, match=r"from 'in_progress' to 'in_progress'"):
        move(task, "in_progress", by=who("start", name="ernie-c"))


# ---------------------------------------------------------------------------------------------
# The states a task is in
# ---------------------------------------------------------------------------------------------


def test_a_new_task_is_open() -> None:
    task = TaskRecord(project="udl", feature="F-1", key="26.3", title="x")

    assert task.state == OPEN
    assert task.assignee is None


def test_a_tasks_state_is_read_as_its_own() -> None:
    assert (
        TaskRecord(project="udl", feature="F-1", key="26.3", title="x", state=READY).state == READY
    )


def test_a_task_may_not_be_in_a_state_there_is_not() -> None:
    with pytest.raises(NotAllowed, match="not a state a task is in"):
        move(task_in(OPEN), "wip", by=who("human"))


# ---------------------------------------------------------------------------------------------
# The helpers a test uses
# ---------------------------------------------------------------------------------------------


def who(role: str, *, name: str = "claude") -> Principal:
    """Who is moving a task: a person (`human`, by `name`), or an agent called `name`.

    The reviewer is the agent called `reviewer`, so a role's own name is who does it."""
    doing: Role | None = role if role in ROLE else None
    if role == "human":
        return Principal(kind="human", name=f"human:{name}")
    if name != "claude":
        return Principal(kind="agent", name=name, role=doing)
    return Principal(kind="agent", name="reviewer" if role == "review" else role, role=doing)


def people(principal: Principal) -> bool:
    """Whether a caller is a person (the `human` role), as the table has it."""
    return principal.kind == "human"


def task_in(state: str, **fields: object) -> TaskRecord:
    """A task of udl, in `state`, with `fields` set (nobody's, unless a test says whose)."""
    given: dict[str, object] = {
        "project": "udl",
        "feature": "F-1",
        "key": "26.3",
        "title": "x",
        "state": state,
    }
    given.update(fields)
    return TaskRecord.model_validate(given)
