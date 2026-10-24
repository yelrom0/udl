"""
-------------------
- test_manager.py -
-------------------

The automations' operations, through the service: events (listing, emitting, replaying),
subscriptions (saving checks them), actions (saving checks them, and keeps their secrets in the
secret store), runs, and the runtime's status
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.actions import FakeAction, fake_action
from support.clock import FakeClock
from support.interfaces import Harness
from udl.automations.bus import NewEvent
from udl.automations.events import EventStatus
from udl.automations.models import (
    ActionRequest,
    EmitEventRequest,
    EventRequest,
    ListActionsRequest,
    ListEventsRequest,
    ListEventTypesRequest,
    ListRunsRequest,
    ListScriptsRequest,
    ListSubscriptionsRequest,
    ReplayEventRequest,
    RunActionRequest,
    RunRequest,
    SetActionRequest,
    SetSubscriptionRequest,
    StatusRequest,
    SubscriptionRequest,
    SubscriptionTestRequest,
)
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.runtime import AutomationState
from udl.config import AutomationsSettings
from udl.core.operations import Caller, current_caller
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import (
    AlreadyExists,
    ConfigError,
    DoesNotExist,
    InUse,
    InvalidFilter,
    NotAllowed,
    PluginError,
    TemplateFailed,
)
from udl.plugins.actions.base import ActionContext, ActionOptions, ActionOutput, ActionPlugin
from udl.plugins.actions.emit import EmitAction
from udl.plugins.actions.webhook import WebhookAction
from udl.plugins.registry import ACTIONS
from udl.plugins.requirements import Requirement
from udl.plugins.secrets.base import SecretField, SecretRef


class NeedsAProgram(FakeAction):
    name = "needy"
    requires = (Requirement.binary("udl-no-such-program", hint="Install it."),)


class KeyedOptions(ActionOptions):
    token: SecretField | None = None
    """A secret."""
    tokens: dict[str, SecretField] | None = None
    """Secrets."""


class Keyed(ActionPlugin[KeyedOptions]):
    """An action kind with a secret option of its own."""

    name = "keyed"
    options_model = KeyedOptions

    async def run(self, ctx: ActionContext[KeyedOptions]) -> ActionOutput:
        raise AssertionError


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake() -> type[FakeAction]:
    return fake_action("fake")


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock, fake: type[FakeAction]) -> Harness:
    found = Harness(
        tmp_path, clock=clock, automations=AutomationsSettings(scripts_dir=tmp_path / "scripts")
    )
    for plugin in (fake, EmitAction, WebhookAction, NeedsAProgram, Keyed):
        found.registry.register(ACTIONS, plugin)
    return found


@pytest.fixture
async def service(harness: Harness) -> AsyncIterator[UDLService]:
    async with harness.service() as found:
        yield found


async def action(service: UDLService, name: str = "hello", **fields: Any) -> Any:
    fields.setdefault("plugin", "fake")
    return await service.set_action(SetActionRequest.model_validate({"name": name, **fields}))


async def subscribe(service: UDLService, name: str = "greet", **fields: Any) -> Any:
    fields.setdefault("events", ["custom.hello"])
    fields.setdefault("actions", ["hello"])
    return await service.set_subscription(
        SetSubscriptionRequest.model_validate({"name": name, **fields})
    )


async def emit(service: UDLService, type_: str = "custom.hello", **data: Any) -> Any:
    return await service.emit_event(EmitEventRequest(type=type_, data=data))


# ---------------------------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------------------------


async def test_the_event_types_are_listed(service: UDLService) -> None:
    listed = await service.list_event_types(ListEventTypesRequest(match="udl.download.*"))

    assert [found.name for found in listed.types] == [
        "udl.download.failed",
        "udl.download.finished",
    ]


async def test_an_emitted_event_is_stored_as_its_caller_s(service: UDLService) -> None:
    token = current_caller.set(Caller.API)
    try:
        emitted = await emit(service, name="x")
    finally:
        current_caller.reset(token)
    from_python = await emit(service, name="y")

    assert (emitted.type, emitted.data, emitted.source) == ("custom.hello", {"name": "x"}, "api")
    assert emitted.status is EventStatus.PENDING
    assert from_python.source == "udl"


async def test_an_event_emitted_again_is_the_first(service: UDLService) -> None:
    first = await service.emit_event(EmitEventRequest(type="custom.hello", dedupe_key="k"))

    again = await service.emit_event(
        EmitEventRequest(type="custom.hello", dedupe_key="k", data={"other": 1})
    )

    assert again == first


@pytest.mark.parametrize("type_", ["udl.download.finished", "hello", "custom."])
def test_only_custom_events_are_emitted(type_: str) -> None:
    with pytest.raises(ValueError, match="custom"):
        EmitEventRequest(type=type_)


async def test_events_are_listed_newest_first_and_filtered(
    service: UDLService, clock: FakeClock
) -> None:
    hello = await emit(service)
    await clock.advance(timedelta(seconds=1))
    bye = await emit(service, "custom.bye")
    await clock.advance(timedelta(seconds=1))
    await service.bus.publish(NewEvent(type="custom.other", source="cli"))

    every = await service.list_events(ListEventsRequest())
    customs = await service.list_events(ListEventsRequest(type="custom.b*"))
    exact = await service.list_events(ListEventsRequest(type="custom.hello"))
    from_cli = await service.list_events(ListEventsRequest(source="cli"))
    paged = await service.list_events(ListEventsRequest(limit=1, offset=1))
    since = await service.list_events(ListEventsRequest(since=bye.stored_at))

    assert [found.type for found in every.events] == ["custom.other", "custom.bye", "custom.hello"]
    assert [found.id for found in customs.events] == [bye.id]
    assert [found.id for found in exact.events] == [hello.id]
    assert [found.type for found in from_cli.events] == ["custom.other"]
    assert [found.id for found in paged.events] == [bye.id]
    assert len(since.events) == 2


async def test_an_event_is_shown_with_its_runs(service: UDLService) -> None:
    await action(service)
    await subscribe(service)
    event = await emit(service)
    await service.bus.dispatch_pending()

    detail = await service.get_event(EventRequest(id=event.id))

    assert detail.event.status is EventStatus.DISPATCHED
    assert [run.action for run in detail.runs] == ["hello"]


async def test_an_unknown_event_does_not_exist(service: UDLService) -> None:
    with pytest.raises(DoesNotExist):
        await service.get_event(EventRequest(id=uuid4()))


async def test_an_event_is_replayed(service: UDLService) -> None:
    await action(service)
    await subscribe(service)
    await subscribe(service, "other")
    event = await emit(service)
    await service.bus.dispatch_pending()

    replayed = await service.replay_event(ReplayEventRequest(id=event.id))
    just_one = await service.replay_event(ReplayEventRequest(id=event.id, subscription="other"))

    assert len(replayed.runs) == 2
    assert [run.subscription for run in just_one.runs] == ["other"]
    with pytest.raises(DoesNotExist):
        await service.replay_event(ReplayEventRequest(id=event.id, subscription="nope"))


# ---------------------------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------------------------


async def test_a_subscription_is_saved_and_shown(service: UDLService) -> None:
    await action(service)

    saved = await subscribe(service, filter='name == "x"', throttle="5 per 1h")
    shown = await service.get_subscription(SubscriptionRequest(name="greet"))

    assert saved == shown
    assert shown.filter_text == 'name == "x"'
    assert str(shown.throttle) == "5 per 1h"


async def test_saving_a_subscription_again_changes_it(service: UDLService) -> None:
    await action(service)
    first = await subscribe(service)

    again = await subscribe(service, events=["custom.*"], enabled=False)

    assert again.id == first.id
    assert (again.events, again.enabled) == (["custom.*"], False)
    listed = await service.list_subscriptions(ListSubscriptionsRequest())
    assert [found.name for found in listed.subscriptions] == ["greet"]


async def test_subscriptions_are_listed_by_name(service: UDLService) -> None:
    await action(service)
    for name in ("b", "a", "c"):
        await subscribe(service, name, enabled=name != "c")

    every = await service.list_subscriptions(ListSubscriptionsRequest())
    enabled = await service.list_subscriptions(ListSubscriptionsRequest(enabled=True))

    assert [found.name for found in every.subscriptions] == ["a", "b", "c"]
    assert [found.name for found in enabled.subscriptions] == ["a", "b"]


async def test_saving_a_subscription_checks_it(service: UDLService) -> None:
    await action(service)

    with pytest.raises(ConfigError, match="nope"):
        await subscribe(service, actions=["nope"])
    with pytest.raises(InvalidFilter):
        await subscribe(service, events=["udl.download.finished"], filter="nope == 1")
    with pytest.raises(AlreadyExists):
        await subscribe(service, "schedule:morning")


async def test_saving_a_subscription_checks_its_actions_with_its_types(
    service: UDLService,
) -> None:
    await action(service, options={"summary": "{{ event.data.url }}"})

    await subscribe(service, events=["udl.download.*"])
    with pytest.raises(TemplateFailed, match="url"):
        await subscribe(service, events=["udl.action.succeeded"])


async def test_a_subscription_is_deleted(service: UDLService) -> None:
    await action(service)
    await subscribe(service)

    deleted = await service.delete_subscription(SubscriptionRequest(name="greet"))
    again = await service.delete_subscription(SubscriptionRequest(name="greet"))

    assert (deleted.deleted, again.deleted) == (True, False)
    with pytest.raises(DoesNotExist):
        await service.get_subscription(SubscriptionRequest(name="greet"))


async def test_a_subscription_is_tried_on_the_latest_events(
    service: UDLService, clock: FakeClock
) -> None:
    await action(service)
    await subscribe(service, filter='name == "x"')
    await emit(service, name="y")
    await clock.advance(timedelta(seconds=1))
    await emit(service, name="x")
    await emit(service, "custom.bye")

    report = await service.try_subscription(SubscriptionTestRequest(name="greet"))

    assert report.filter == 'name == "x"'
    assert report.problems == []
    assert [(found.event.data, found.matched, found.failed) for found in report.events] == [
        ({"name": "x"}, True, None),
        ({"name": "y"}, False, 'name == "x"'),
    ]


async def test_a_subscription_is_tried_unsaved_on_one_event(service: UDLService) -> None:
    event = await emit(service, name="x")
    other = await emit(service, "custom.bye")

    report = await service.try_subscription(
        SubscriptionTestRequest.model_validate(
            {"events": ["custom.hello"], "filter": "name exists", "event": event.id}
        )
    )
    wrong_type = await service.try_subscription(
        SubscriptionTestRequest(events=["custom.hello"], event=other.id)
    )
    accounts = await service.try_subscription(
        SubscriptionTestRequest(events=["custom.*"], accounts=["gmail:work"], event=event.id)
    )

    assert [found.matched for found in report.events] == [True]
    assert wrong_type.events[0].failed == "its type"
    assert accounts.events[0].failed == "its account"


async def test_trying_a_subscription_says_its_problems(service: UDLService) -> None:
    await action(service)
    await subscribe(service, enabled=False)

    disabled = await service.try_subscription(SubscriptionTestRequest(name="greet"))
    wrong = await service.try_subscription(
        SubscriptionTestRequest.model_validate(
            {"events": ["udl.download.finished", "nothing.at.all"], "filter": "nope"}
        )
    )

    assert "disabled" in disabled.problems[0]
    assert any("nothing.at.all" in problem for problem in wrong.problems)
    assert any("nope" in problem for problem in wrong.problems)


async def test_a_subscription_to_try_names_its_types(service: UDLService) -> None:
    with pytest.raises(ConfigError, match="event types"):
        await service.try_subscription(SubscriptionTestRequest())


# ---------------------------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------------------------


async def test_an_action_is_saved_and_shown(service: UDLService) -> None:
    saved = await action(service, options={"output": 1}, description="Says hello")
    await subscribe(service)

    shown = await service.get_action(ActionRequest(name="hello"))

    assert (shown.id, shown.options, shown.description) == (saved.id, {"output": 1}, "Says hello")
    assert shown.used_by == ["greet"]
    assert "outcome" in cast(dict[str, object], shown.options_schema["properties"])
    assert shown.warnings == []


async def test_saving_an_action_again_changes_it(service: UDLService) -> None:
    first = await action(service)

    again = await action(service, retries=3)

    assert (again.id, again.retries) == (first.id, 3)


async def test_actions_are_listed_by_name_and_kind(service: UDLService) -> None:
    await action(service, "b")
    await action(service, "a", plugin="emit", options={"type": "custom.x"})

    every = await service.list_actions(ListActionsRequest())
    emits = await service.list_actions(ListActionsRequest(plugin="emit"))

    assert [found.name for found in every.actions] == ["a", "b"]
    assert [found.name for found in emits.actions] == ["a"]


async def test_an_action_s_warnings_say_what_keeps_it_from_running(
    service: UDLService,
) -> None:
    needy = await action(service, "needy", plugin="needy")
    off = await action(service, "off", enabled=False)
    await service.db.upsert_record(
        (await service.automations._action("off")).model_copy(update={"plugin": "gone"})  # pyright: ignore[reportPrivateUsage]
    )

    gone = await service.get_action(ActionRequest(name="off"))

    assert needy.warnings == ["The needy kind needs udl-no-such-program: Install it."]
    assert "disabled" in off.warnings[0]
    assert any("gone" in warning for warning in gone.warnings)
    assert gone.options_schema == {}


async def test_long_lists_are_read_a_page_at_a_time(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("udl.automations.manager._SCAN", 2)
    for n in range(5):
        await emit(service, "custom.hello" if n % 2 else "custom.bye", n=n)

    found = await service.list_events(ListEventsRequest(type="custom.h*", limit=10))
    first = await service.list_events(ListEventsRequest(type="custom.h*", limit=1))

    assert sorted(cast(int, event.data["n"]) for event in found.events) == [1, 3]
    assert len(first.events) == 1


def test_an_action_s_timeout_is_some_time() -> None:
    with pytest.raises(ValueError, match="timeout"):
        SetActionRequest.model_validate({"name": "x", "plugin": "fake", "timeout": "0s"})


async def test_saving_an_action_checks_it(service: UDLService) -> None:
    with pytest.raises(PluginError, match="nothing"):
        await action(service, plugin="nothing")
    with pytest.raises(ConfigError, match="outcome"):
        await action(service, options={"outcome": "explode"})
    with pytest.raises(ValueError, match="scripts"):
        SetActionRequest(name="scripts", plugin="fake")


async def test_saving_an_action_checks_it_with_its_subscriptions_types(
    service: UDLService,
) -> None:
    await action(service)
    await subscribe(service, events=["udl.download.finished"])

    await action(service, options={"summary": "{{ event.data.url }}"})
    with pytest.raises(TemplateFailed, match="nope"):
        await action(service, options={"summary": "{{ event.data.nope }}"})


async def test_an_action_s_secrets_go_to_the_secret_store(service: UDLService) -> None:
    saved = await action(
        service,
        "hook",
        plugin="webhook",
        options={
            "url": "https://hooks.example.com/x",
            "secret_headers": {"Authorization": "Bearer s3cret"},
        },
    )

    ref = SecretRef.of("actions", saved.id, "secret_headers", "Authorization")
    assert saved.options["secret_headers"] == {"Authorization": {"secret": str(ref)}}
    assert "s3cret" not in saved.model_dump_json()
    found = await service.secrets.get(ref)
    assert found is not None
    assert found.get_secret_value() == "Bearer s3cret"


async def test_a_secret_option_of_its_own_is_kept_and_hidden(service: UDLService) -> None:
    saved = await action(service, "keyed", plugin="keyed", options={"token": "t0ken"})
    dry = await service.run_action(RunActionRequest(name="keyed", dry_run=True))
    empty = await action(service, "empty", plugin="keyed", options={"tokens": "{{ event.x }}"})

    ref = SecretRef.of("actions", saved.id, "token")
    assert saved.options["token"] == {"secret": str(ref)}
    assert dry.options is not None
    assert (dry.options["token"], dry.options["tokens"]) == ("***", None)
    assert empty.options == {"tokens": "{{ event.x }}"}  # (no secret in it to keep)


async def test_a_kept_secret_given_back_is_kept_and_one_left_out_forgotten(
    service: UDLService,
) -> None:
    first = await action(
        service,
        "hook",
        plugin="webhook",
        options={
            "url": "https://hooks.example.com/x",
            "secret_headers": {"Authorization": "Bearer s3cret", "X-Key": "k3y"},
        },
    )
    kept = first.options["secret_headers"]["Authorization"]

    again = await action(
        service,
        "hook",
        plugin="webhook",
        options={"url": "https://hooks.example.com/y", "secret_headers": {"Authorization": kept}},
    )

    auth = SecretRef.of("actions", first.id, "secret_headers", "Authorization")
    key = SecretRef.of("actions", first.id, "secret_headers", "X-Key")
    assert again.options["secret_headers"] == {"Authorization": kept}
    found = await service.secrets.get(auth)
    assert found is not None
    assert found.get_secret_value() == "Bearer s3cret"
    assert await service.secrets.get(key) is None


async def test_an_action_never_takes_another_s_secret(service: UDLService) -> None:
    await service.secrets.set(SecretRef("plugins/gmail/client_secret"), SecretStr("theirs"))

    with pytest.raises(NotAllowed, match="not this action's"):
        await action(
            service,
            "thief",
            plugin="webhook",
            options={
                "url": "https://evil.example.com/",
                "secret_headers": {"X": {"secret": "plugins/gmail/client_secret"}},
            },
        )


async def test_a_kept_secret_the_store_lost_is_refused(service: UDLService) -> None:
    saved = await action(
        service,
        "hook",
        plugin="webhook",
        options={"url": "https://hooks.example.com/x", "secret_headers": {"X": "v"}},
    )
    kept = saved.options["secret_headers"]["X"]
    await service.secrets.delete_prefix(SecretRef.of("actions", saved.id))

    with pytest.raises(ConfigError, match="no actions"):
        await action(
            service,
            "hook",
            plugin="webhook",
            options={"url": "https://hooks.example.com/x", "secret_headers": {"X": kept}},
        )


async def test_an_action_is_deleted_with_its_secrets(service: UDLService) -> None:
    saved = await action(
        service,
        "hook",
        plugin="webhook",
        options={"url": "https://hooks.example.com/x", "secret_headers": {"X": "v"}},
    )

    deleted = await service.delete_action(ActionRequest(name="hook"))
    again = await service.delete_action(ActionRequest(name="hook"))

    assert (deleted.deleted, again.deleted) == (True, False)
    assert (
        await service.secrets.get(SecretRef.of("actions", saved.id, "secret_headers", "X")) is None
    )


async def test_an_action_a_subscription_runs_is_not_deleted(service: UDLService) -> None:
    await action(service)
    await subscribe(service)

    with pytest.raises(InUse, match="greet"):
        await service.delete_action(ActionRequest(name="hello"))


async def test_an_unknown_action_does_not_exist(service: UDLService) -> None:
    with pytest.raises(DoesNotExist):
        await service.get_action(ActionRequest(name="nope"))


async def test_an_action_runs_now(service: UDLService, fake: type[FakeAction]) -> None:
    await action(service, options={"output": "{{ event.data.name }}"})
    event = await emit(service, name="x")

    done = await service.run_action(RunActionRequest(name="hello", event=event.id))

    assert done.run is not None
    assert (done.run.status, done.run.output, done.options) == (RunStatus.SUCCEEDED, "x", None)
    assert len(fake.ran) == 1


async def test_a_dry_run_only_renders_the_options(
    service: UDLService, fake: type[FakeAction]
) -> None:
    await action(
        service,
        "hook",
        plugin="webhook",
        options={
            "url": "https://hooks.example.com/{{ event.data.name }}",
            "secret_headers": {"X": "v"},
        },
    )
    event = await emit(service, name="x")

    done = await service.run_action(RunActionRequest(name="hook", event=event.id, dry_run=True))

    assert done.run is None
    assert done.options is not None
    assert done.options["url"] == "https://hooks.example.com/x"
    assert done.options["secret_headers"] == {"X": "***"}
    assert await service.db.list_records(ActionRun, RecordQuery()) == []


async def test_the_scripts_are_listed(service: UDLService, tmp_path: Path) -> None:
    missing = await service.list_scripts(ListScriptsRequest())
    folder = tmp_path / "scripts"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.sh").write_text("#!/bin/sh\n")
    (folder / "a.sh").chmod(0o755)
    (folder / "sub" / "b.py").write_text("print()\n")
    (folder / ".hidden").write_text("x")

    listed = await service.list_scripts(ListScriptsRequest())

    assert (missing.exists, missing.scripts) == (False, [])
    assert listed.exists
    assert [(found.name, found.executable) for found in listed.scripts] == [
        ("a.sh", True),
        ("sub/b.py", False),
    ]
    assert listed.scripts[0].size == len("#!/bin/sh\n")


# ---------------------------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------------------------


async def test_runs_are_listed_shown_and_cancelled(service: UDLService) -> None:
    await action(service)
    await action(service, "other")
    await subscribe(service, actions=["hello", "other"])
    event = await emit(service)
    await service.bus.dispatch_pending()

    every = await service.list_runs(ListRunsRequest())
    hellos = await service.list_runs(ListRunsRequest(action="hello"))
    of_event = await service.list_runs(ListRunsRequest(event=event.id, status=RunStatus.QUEUED))
    shown = await service.get_run(RunRequest(id=hellos.runs[0].id))
    cancelled = await service.cancel_run(RunRequest(id=shown.id))

    assert len(every.runs) == 2
    assert [found.action for found in hellos.runs] == ["hello"]
    assert len(of_event.runs) == 2
    assert (shown.action, shown.status, shown.log) == ("hello", RunStatus.QUEUED, "")
    assert cancelled.status is RunStatus.CANCELLED
    with pytest.raises(DoesNotExist):
        await service.get_run(RunRequest(id=uuid4()))


async def test_the_status_is_given(service: UDLService) -> None:
    status = await service.get_status(StatusRequest())

    assert status.automations is AutomationState.STOPPED
