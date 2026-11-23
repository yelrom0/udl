"""
------------------
- test_chat.py -
------------------

A person's chat with their own harness (27.11), the Claude Code half: starting one, sending it a
turn, being asked before it acts, stopping it, and the token that reaches that one chat.

A chat is not an agent's session. It is the person's own harness, in their folder, with the
options they chose, and its transcript is the same `SessionEvent`s an agent's session keeps — so
the live view and the transcript are shared code. What udl answers while a turn runs is the
harness's permission prompt: udl's MCP tool `orch_chat_ask_permission`, which the harness is
pointed at with `--permission-prompt-tool`.

These run through `service.call()`, as every interface calls the operations, so what is tested is
the operations and what they write.
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator
from contextvars import Token
from datetime import timedelta
from pathlib import Path
from typing import ClassVar
from uuid import UUID

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.clock import FakeClock
from support.engine import registry, settings
from support.runners import FakeRunner, FakeRunnerOptions, Sessions, Turn
from udl.automations.models import EventList
from udl.config import APISettings
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import NotAllowed, UDLError
from udl.hosts.shell import HostShell
from udl.interfaces.security import (
    CHAT_PREFIX,
    AgentTokens,
    Principal,
    PrincipalKind,
    authenticate,
    current_principal,
)
from udl.plugins.apps.base import AppEvents, AppRecords
from udl.plugins.apps.orch.agents import SessionState
from udl.plugins.apps.orch.chat import (
    AskPermissionRequest,
    ChatInfo,
    ChatList,
    ChatPermissionInfo,
    Chats,
    PermissionAnswer,
    chat_secret_ref,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Chat,
    ChatPermission,
    ChatRecords,
    ChatTokenRecords,
    SessionEvent,
)
from udl.plugins.apps.orch.tokens import tokens_for
from udl.plugins.registry import APPS, RUNNERS, PluginRegistry
from udl.plugins.runners.base import AgentEvent, SessionRef, TextDelta, TurnEnded, TurnReason
from udl.plugins.runners.manage import Harnesses
from udl.plugins.secrets.base import SecretRef

SCRIPT: dict[str, Turn] = {}
"""What each prompt's turn does (a test scripts it)."""
STORE = Sessions()
"""The chats' sessions (they outlive the runner, as a real harness's do)."""
SERVICE: list[UDLService] = []
"""The service a scripted turn calls, so a harness can ask udl as its own tool would."""


class ChatRunner(FakeRunner):
    """`FakeRunner`, made by the harnesses' own factory: it reads this module's script."""

    name: ClassVar[str] = "fake"

    def __init__(self, options: FakeRunnerOptions, *, shell: HostShell, clock: FakeClock) -> None:
        super().__init__(options, shell=shell, clock=clock, script=SCRIPT, store=STORE)


class BrokenRunner(FakeRunner):
    """A harness that cannot start (its own program is not there): a chat that dies at once."""

    name: ClassVar[str] = "broken"

    async def start(self, spec: object) -> SessionRef:
        raise RuntimeError("no harness here")


def orch_registry(*, broken: bool = False) -> PluginRegistry:
    """The memory database, the secret store, the fake harness, and orch."""
    plugins = registry()
    plugins.register(RUNNERS, ChatRunner)
    if broken:
        plugins.register(RUNNERS, BrokenRunner)
    plugins.register(APPS, Orch)
    return plugins


@pytest.fixture(autouse=True)
def fresh() -> None:
    """Each test starts with its own script and sessions (and tells the turns its service)."""
    SCRIPT.clear()
    STORE.specs.clear()
    STORE.stopped.clear()
    STORE.turns = 0
    STORE.gates.clear()
    SERVICE.clear()


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with the fake harness, and a folder to chat in."""
    async with UDLService(
        settings(tmp_path, api=APISettings(api_key=SecretStr("test-key"))),
        registry=orch_registry(),
    ) as running:
        SERVICE.append(running)
        yield running
        await end_chats(running)


@pytest.fixture
async def broken(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service whose harness cannot start."""
    async with UDLService(
        settings(tmp_path, api=APISettings(api_key=SecretStr("test-key"))),
        registry=orch_registry(broken=True),
    ) as running:
        SERVICE.append(running)
        yield running
        await end_chats(running)


async def end_chats(service: UDLService) -> None:
    """End every chat a test left running, so no turn outlives the service it writes to."""
    found = await service.call("orch_chat_list", {"all": True})
    assert isinstance(found, ChatList)
    for chat in found.items:
        if chat.state not in (SessionState.ENDED, SessionState.FAILED):
            await service.call("orch_chat_stop", {"chat": chat.id})


def as_owner() -> Token[Principal | None]:
    """The caller is udl's owner for a while."""
    return current_principal.set(Principal(kind=PrincipalKind.OWNER, name="owner"))


async def started(service: UDLService, **given: object) -> ChatInfo:
    """Start a chat in the test's folder, as the owner."""
    token = as_owner()
    try:
        found: object = await service.call(
            "orch_chat_start",
            {"runner": "fake", "directory": "/work/udl", "model": "fake-1", **given},
        )
    finally:
        current_principal.reset(token)
    assert isinstance(found, ChatInfo)
    return found


async def settled(service: UDLService, chat: ChatInfo, *, turns: int = 1) -> ChatInfo:
    """Wait for the chat's turn to end, and answer it."""
    found = chat
    for _ in range(200):
        read: object = await service.call("orch_chat_get", {"chat": chat.id})
        assert isinstance(read, ChatInfo)
        found = read
        if found.turns >= turns or found.state in (SessionState.FAILED, SessionState.ENDED):
            return found
        await asyncio.sleep(0.01)
    raise AssertionError(f"the chat never settled: {found.state} ({found.last_error})")


# ---------------------------------------------------------------------------------------------
# The card's done-when
# ---------------------------------------------------------------------------------------------


async def test_the_owner_starts_a_chat_and_sends_it_a_turn(service: UDLService) -> None:
    """(The card's done-when: a person starts a chat with their own harness, sends it a turn,
    and reads the answer in its transcript.)"""

    async def hello(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
        yield TextDelta(text="Hello")
        yield TextDelta(text=" — the queue is fine.")
        yield TurnEnded(reason=TurnReason.DONE, text="Hello — the queue is fine.")

    SCRIPT["why is the queue stuck?"] = hello

    chat = await started(service)
    assert chat.state is SessionState.IDLE
    assert chat.permission_mode == "manual", "a chat may ask before it acts"

    await service.call("orch_chat_send", {"chat": chat.id, "text": "why is the queue stuck?"})
    done = await settled(service, chat)

    assert done.state is SessionState.IDLE
    assert done.turns == 1
    assert done.last_text == "Hello — the queue is fine."
    assert done.title == "why is the queue stuck?", "the first thing said is what it is about"
    said = [event for event in done.events if isinstance(event, TextDelta)]
    assert [event.text for event in said] == ["Hello", " — the queue is fine."]
    assert STORE.specs, "the harness was started"
    spec = next(iter(STORE.specs.values()))
    assert spec.cwd == "/work/udl"
    assert spec.model == "fake-1"
    assert spec.own_settings is True, "a chat runs as the person would"
    assert spec.permission_mode is None, "the person's own settings decide"
    assert spec.permission_tool == "mcp__udl__orch_chat_ask_permission"
    assert spec.mcp is not None, "the harness is pointed at udl's MCP server"


async def test_a_permission_is_asked_and_the_answer_reaches_the_harness(
    service: UDLService,
) -> None:
    """(The card's second done-when: the harness asks before it acts, the person answers, and
    the answer reaches the harness — allowed, or denied with why.)"""
    answer: list[PermissionAnswer] = []

    async def wants_bash(runner: FakeRunner, session: SessionRef) -> AsyncIterator[AgentEvent]:
        chat = await chat_of(service)
        asked = await asked_by_harness(
            service, chat, tool="Bash", given={"command": "rm -rf build"}
        )
        answer.append(asked)
        yield TextDelta(text="Fine." if asked.behavior == "allow" else f"No: {asked.message}")
        yield TurnEnded(reason=TurnReason.DONE)

    SCRIPT["clear the build"] = wants_bash
    chat = await started(service)
    await service.call("orch_chat_send", {"chat": chat.id, "text": "clear the build"})

    asked = await waiting_permission(service, chat)
    assert asked.tool == "Bash"
    assert asked.input == {"command": "rm -rf build"}
    assert asked.state == "pending"

    await service.call(
        "orch_chat_permission",
        {
            "chat": str(chat.id),
            "request": str(asked.id),
            "decision": "deny",
            "message": "not that folder",
        },
    )
    done = await settled(service, chat)
    assert answer
    assert answer[0].behavior == "deny"
    assert answer[0].message == "not that folder"
    assert done.last_text == "No: not that folder"


async def test_a_permission_allowed_for_the_chat_is_not_asked_again(
    service: UDLService,
) -> None:
    """(The card's third permission test: Allow for this chat answers the same request by itself
    the second time, but asks again for `git push` after `git status` and for `ls && rm x` after
    `ls`.)"""
    asked_for: list[str] = []

    async def runs(runner: FakeRunner, session: SessionRef) -> AsyncIterator[AgentEvent]:
        chat = await chat_of(service)
        for command in ("ls", "ls", "git status", "git push", "ls && rm x"):
            asked = await asked_by_harness(service, chat, tool="Bash", given={"command": command})
            asked_for.append(command if asked.behavior == "deny" else f"{command} (allowed)")
            if asked.behavior == "deny":
                break  # (a test that is waiting for a person's answer stops here)
        yield TurnEnded(reason=TurnReason.DONE)

    SCRIPT["run some commands"] = runs
    chat = await started(service)
    await service.call("orch_chat_send", {"chat": chat.id, "text": "run some commands"})

    # (ls: asked, allowed for this chat.)
    first = await waiting_permission(service, chat)
    assert first.tool == "Bash"
    await service.call(
        "orch_chat_permission",
        {"chat": str(chat.id), "request": str(first.id), "decision": "allow_chat"},
    )
    # (ls again: answered by itself; git status: a new request.)
    second = await waiting_permission(service, chat, but_not=first.id)
    assert second.input == {"command": "git status"}, "a different command asks again"
    await service.call(
        "orch_chat_permission",
        {"chat": str(chat.id), "request": str(second.id), "decision": "allow_chat"},
    )
    # (git push: asked; and ls && rm x after it is asked too, not remembered.)
    third = await waiting_permission(service, chat, but_not=second.id)
    assert third.input == {"command": "git push"}
    await service.call(
        "orch_chat_permission",
        {"chat": str(chat.id), "request": str(third.id), "decision": "allow_chat"},
    )
    fourth = await waiting_permission(service, chat, but_not=third.id)
    assert fourth.input == {"command": "ls && rm x"}, "a chained command is never remembered"
    await service.call(
        "orch_chat_permission",
        {"chat": str(chat.id), "request": str(fourth.id), "decision": "deny"},
    )
    done = await settled(service, chat)
    assert done.state is SessionState.IDLE
    assert asked_for == [
        "ls (allowed)",
        "ls (allowed)",
        "git status (allowed)",
        "git push (allowed)",
        "ls && rm x",  # (denied: a chained command is asked every time)
    ]


async def test_what_is_said_during_a_turn_is_the_next_turn(service: UDLService) -> None:
    """(27.11: a chat takes one turn at a time, so a message sent during one is queued, and runs
    when the turn ends.)"""
    release = asyncio.Event()

    async def slow(runner: FakeRunner, session: SessionRef) -> AsyncIterator[AgentEvent]:
        yield TextDelta(text="Working.")
        await release.wait()
        yield TurnEnded(reason=TurnReason.DONE)

    async def quick(runner: FakeRunner, session: SessionRef) -> AsyncIterator[AgentEvent]:
        yield TextDelta(text="And now this.")
        yield TurnEnded(reason=TurnReason.DONE)

    SCRIPT["start"] = slow
    SCRIPT["while you are there"] = quick
    chat = await started(service)
    await service.call("orch_chat_send", {"chat": chat.id, "text": "start"})
    await asyncio.sleep(0.01)

    queued = await service.call("orch_chat_send", {"chat": chat.id, "text": "while you are there"})
    assert isinstance(queued, ChatInfo)
    assert queued.pending == 1, "it is queued, not a second turn at once"

    release.set()
    done = await settled(service, chat, turns=2)
    assert done.pending == 0
    assert done.last_text == "And now this.", "the queued message ran as the next turn"


async def test_a_chat_a_person_stops_stays_stopped(service: UDLService) -> None:
    """(The card's third done-when: a chat a person stops ends, and its harness's session is
    stopped with it.)"""

    async def waits(runner: FakeRunner, session: SessionRef) -> AsyncIterator[AgentEvent]:
        await runner.waiting(session)
        yield TurnEnded(reason=TurnReason.STOPPED)

    SCRIPT["wait"] = waits
    chat = await started(service)
    await service.call("orch_chat_send", {"chat": chat.id, "text": "wait"})
    await asyncio.sleep(0.01)

    stopped = await service.call("orch_chat_stop", {"chat": chat.id})
    assert isinstance(stopped, ChatInfo)
    assert stopped.state is SessionState.ENDED
    assert STORE.stopped, "the harness's session was stopped"

    with pytest.raises(UDLError):
        await service.call("orch_chat_send", {"chat": chat.id, "text": "again"})


async def test_a_chat_that_dies_before_its_harness_starts_is_reported(
    broken: UDLService,
) -> None:
    """(The card's fourth done-when: a chat whose harness never starts says why, and is over.)"""
    token = as_owner()
    try:
        found = await broken.call("orch_chat_start", {"runner": "broken", "directory": "/work"})
    finally:
        current_principal.reset(token)
    assert isinstance(found, ChatInfo)
    assert found.state is SessionState.FAILED
    assert "no harness here" in found.last_error


async def test_a_turn_that_never_starts_ends_the_chat(service: UDLService) -> None:
    """A harness whose turn fails says why, and the chat takes no more turns."""
    chat = await started(service)
    STORE.stopped.add("fake-1")  # (a session the harness does not have any more)
    await service.call("orch_chat_send", {"chat": chat.id, "text": "hello"})
    done = await settled(service, chat)
    assert done.state is SessionState.FAILED
    assert done.last_error, "it says why"
    with pytest.raises(UDLError):
        await service.call("orch_chat_send", {"chat": chat.id, "text": "again"})


# ---------------------------------------------------------------------------------------------
# The token, and who reads a chat
# ---------------------------------------------------------------------------------------------


async def test_the_chat_s_token_does_the_person_s_work_and_not_more(service: UDLService) -> None:
    """(The card's token test: a chat's token is refused `orch_issue_token`, `orch_chat_start`
    and `orch_chat_permission`, calls `orch_chat_ask_permission` for its own chat only, works for
    `orch_assign`, and stops working after `orch_chat_stop`.)"""
    mine = await started(service)
    other = await started(service)
    await given_a_task(service)

    token = await service.secrets.get(chat_secret(mine))
    assert token is not None
    given = token.get_secret_value()
    assert given.startswith(CHAT_PREFIX)

    who = await authenticate(service.settings, given, tokens=orch_tokens(service))
    assert who.kind is PrincipalKind.CHAT
    assert who.chat == mine.id
    assert who.name == "human:owner"

    saved: Token[Principal | None] = current_principal.set(who)
    try:
        with pytest.raises(NotAllowed):
            await service.call("orch_issue_token", {"agent": "deepseek-b"})
        with pytest.raises(NotAllowed):
            await service.call("orch_chat_start", {"runner": "fake"})
        with pytest.raises(NotAllowed):
            await service.call("orch_chat_permission", {"chat": mine.id, "request": mine.id})
        with pytest.raises(NotAllowed):
            await service.call("orch_chat_get", {"chat": mine.id})

        assigned = await service.call(
            "orch_assign", {"project": "udl", "key": "F-1.1", "agent": "deepseek-b"}
        )
        assert assigned is not None, "a chat does the person's work through udl"

        # (A token asks for its own chat, whatever the request names: the chat is its own — and
        # the request cannot name one at all, so the token's chat is the only one it can reach.)
        asking = asyncio.create_task(
            service.call(
                "orch_chat_ask_permission",
                {"tool_name": "Bash", "input": {"command": "ls"}, "tool_use_id": "toolu_2"},
            )
        )
        filed = await pending_of(service, mine)
        assert filed is not None, "the request is the token's own chat's"
        assert await pending_of(service, other) is None, "and never the one it named"
    finally:
        current_principal.reset(saved)

    await service.call(
        "orch_chat_permission",
        {"chat": str(mine.id), "request": str(filed.id), "decision": "allow_once"},
    )
    answer = await asyncio.wait_for(asking, timeout=10)
    assert isinstance(answer, PermissionAnswer)
    assert answer.behavior == "allow"

    # (Stopped: its token is deleted, and no longer resolves.)
    stopped = await service.call("orch_chat_stop", {"chat": mine.id})
    assert isinstance(stopped, ChatInfo)
    assert await service.secrets.get(chat_secret(mine)) is None
    from udl.errors import Unauthorized

    with pytest.raises(Unauthorized):
        await authenticate(service.settings, given, tokens=orch_tokens(service))


async def test_nobody_answering_in_time_is_a_denial(service: UDLService) -> None:
    """(The card's timeout: a prompt nobody answers within `chat_permission_minutes` is denied,
    and the harness is told why — it is never left waiting.)"""
    clock = FakeClock()
    records = AppRecords(service.db)
    chats = Chats(
        service=service,
        records=records,
        events=AppEvents(service.bus, "orch", []),
        harnesses=Harnesses(service),
        clock=clock,
        permission_minutes=10,
    )
    chat = await records.save(Chat(owner="human:owner", runner="fake"))

    asking = asyncio.create_task(
        chats.ask(chat, AskPermissionRequest(tool_name="Bash", input={"command": "ls"}))
    )
    for _ in range(200):
        if await ChatRecords(records).pending(chat.id) is not None:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("the request was never filed")

    await clock.advance(timedelta(minutes=11))
    answer = await asyncio.wait_for(asking, timeout=10)

    assert answer.behavior == "deny"
    assert "10 minutes" in answer.message


async def test_an_agent_s_token_cannot_start_a_chat(service: UDLService) -> None:
    """(The card's last test: starting a chat is the person's, so an agent's token is refused.)"""
    caller = current_principal.set(
        Principal(kind=PrincipalKind.AGENT, name="claude", role="implementer")
    )
    try:
        with pytest.raises(NotAllowed):
            await service.call(
                "orch_chat_start", {"runner": "fake", "directory": "/work/udl", "model": "fake-1"}
            )
    finally:
        current_principal.reset(caller)


async def test_a_token_of_a_chat_that_is_gone_is_unknown(service: UDLService) -> None:
    """A chat that is deleted takes its token with it: udl no longer knows it."""
    chat = await started(service)
    token = await service.secrets.get(chat_secret(chat))
    assert token is not None

    forgotten = await service.call("orch_chat_delete", {"chat": chat.id})
    assert isinstance(forgotten, ChatInfo)
    assert forgotten.id == chat.id

    from udl.errors import Unauthorized

    with pytest.raises(Unauthorized):
        await authenticate(service.settings, token.get_secret_value(), tokens=orch_tokens(service))


async def test_the_chat_s_stream_is_one_chat_s(service: UDLService) -> None:
    """(The card's sixth done-when: the stream carries that chat's events, and no other's.)"""

    async def hello(runner: FakeRunner, session: SessionRef) -> AsyncIterator[AgentEvent]:
        yield TextDelta(text="hi")
        yield TurnEnded(reason=TurnReason.DONE)

    SCRIPT["hello"] = hello
    mine = await started(service)
    other = await started(service)
    await service.call("orch_chat_send", {"chat": mine.id, "text": "hello"})
    await settled(service, mine)

    published: object = await service.call("list_events", {"type": "orch.chat.event", "limit": 200})
    assert isinstance(published, EventList)
    seen = {str(event.data.get("chat")) for event in published.events}
    assert str(mine.id) in seen
    assert str(other.id) not in seen, "the other chat's stream is its own"


# ---------------------------------------------------------------------------------------------
# Reading and forgetting
# ---------------------------------------------------------------------------------------------


async def asked_by_harness(
    service: UDLService, chat: object, *, tool: str, given: dict[str, object]
) -> PermissionAnswer:
    """What a chat's harness asks udl, as it does: with the chat's own token (its principal), and
    in its own protocol (`tool_name`, `input`, `tool_use_id`)."""
    assert isinstance(chat, ChatInfo)
    caller = current_principal.set(
        Principal(kind=PrincipalKind.CHAT, name=chat.owner, chat=chat.id)
    )
    try:
        answer = await service.call(
            "orch_chat_ask_permission",
            {"tool_name": tool, "input": given, "tool_use_id": "toolu_1"},
        )
    finally:
        current_principal.reset(caller)
    assert isinstance(answer, PermissionAnswer)
    return answer


async def test_a_chat_is_read_with_its_permissions_and_its_last_words(
    service: UDLService,
) -> None:
    """A chat is read with what its harness last said, and what it is waiting on."""

    async def asks(runner: FakeRunner, session: SessionRef) -> AsyncIterator[AgentEvent]:
        yield TextDelta(text="Let me edit a.py.")
        chat = await chat_of(service)
        await asked_by_harness(service, chat, tool="Edit", given={"file": "a.py"})
        yield TurnEnded(reason=TurnReason.DONE)

    SCRIPT["edit it"] = asks
    chat = await started(service)
    await service.call("orch_chat_send", {"chat": chat.id, "text": "edit it"})
    asked = await waiting_permission(service, chat)

    read = await service.call("orch_chat_get", {"chat": chat.id})
    assert isinstance(read, ChatInfo)
    assert read.permission is not None
    assert read.permission.id == asked.id
    assert read.permission.tool == "Edit"
    assert read.total >= 1, "its transcript is paged with a total"

    listed = await service.call("orch_chat_list", {})
    assert isinstance(listed, ChatList)
    assert [one.id for one in listed.items] == [chat.id]
    assert listed.items[0].events == [], "a list is the chats, not their transcripts"

    every = await service.call("orch_chat_list", {"all": True})
    assert isinstance(every, ChatList)
    assert len(every.items) == 1


async def test_deleting_a_chat_forgets_its_transcript(service: UDLService) -> None:
    """A deleted chat is gone: its record, its transcript and what it asked."""

    async def hello(runner: FakeRunner, session: SessionRef) -> AsyncIterator[AgentEvent]:
        yield TextDelta(text="hi")
        yield TurnEnded(reason=TurnReason.DONE)

    SCRIPT["hello"] = hello
    chat = await started(service)
    await service.call("orch_chat_send", {"chat": chat.id, "text": "hello"})
    await settled(service, chat)

    records = AppRecords(service.db)
    assert await ChatRecords(records).by_id(chat.id) is not None
    await service.call("orch_chat_delete", {"chat": chat.id})
    assert await ChatRecords(records).by_id(chat.id) is None
    assert await ChatTokenRecords(records).for_chat(chat.id) == [], "its token went with it"
    assert (
        await service.db.list_records(SessionEvent, RecordQuery(where={"session": chat.id})) == []
    )


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


async def pending_of(service: UDLService, chat: ChatInfo) -> ChatPermission | None:
    """What `chat`'s harness is waiting on (as the records hold it, once it has asked)."""
    for _ in range(200):
        found = await ChatRecords(AppRecords(service.db)).pending(chat.id)
        if found is not None:
            return found
        await asyncio.sleep(0.01)
    return None


async def given_a_task(service: UDLService) -> None:
    """A project, a feature, an agent and a queued task, for the token to assign."""
    from udl.plugins.apps.base import AppRecords as Records
    from udl.plugins.apps.orch.records import (
        Agent,
        AgentRecords,
        Feature,
        FeatureRecords,
        Project,
        ProjectRecords,
        Task,
        TaskRecords,
    )

    records = Records(service.db)
    await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
    await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="Chats"))
    await AgentRecords(records).save_agent(
        Agent(name="deepseek-b", title="deepseek-b", runner="fake", host="local", brief="Build it.")
    )
    await TaskRecords(records).save_task(
        Task(project="udl", feature="F-1", key="F-1.1", title="A task")
    )


async def chat_of(service: UDLService) -> ChatInfo:
    """The chat this test started (a scripted turn reads it, as its tool call's chat)."""
    found = await service.call("orch_chat_list", {"all": True})
    assert isinstance(found, ChatList)
    assert found.items
    return found.items[0]


async def waiting_permission(
    service: UDLService, chat: ChatInfo, *, but_not: UUID | None = None
) -> ChatPermissionInfo:
    """What the chat's harness is waiting on (something other than `but_not`)."""
    for _ in range(200):
        read: object = await service.call("orch_chat_get", {"chat": chat.id})
        assert isinstance(read, ChatInfo)
        if read.permission is not None and read.permission.id != but_not:
            return read.permission
        await asyncio.sleep(0.01)
    raise AssertionError("the harness never asked")


def chat_secret(chat: ChatInfo) -> SecretRef:
    """Where `chat`'s token is kept."""
    return chat_secret_ref(chat.id)


def orch_tokens(service: UDLService) -> AgentTokens:
    """The book udl asks who a token is."""
    return tokens_for(service)
