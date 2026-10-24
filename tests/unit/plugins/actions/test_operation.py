"""
---------------------
- test_operation.py -
---------------------

The operation action: calls one of udl's operations (or a plugin's) with arguments rendered
from the event, validated by the operation's request model when the action is saved (with an
event type's example) and again when it runs. An admin operation (one that changes automations,
accounts or settings) is never one it calls. It runs as the automations do (current_caller), so
paths are held to their roots.
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

# Package Imports
import pytest

# Local Imports
from support.actions import with_chores
from support.interfaces import Harness
from udl.automations.events import EventTypes
from udl.core.service import UDLService
from udl.db.models import JsonObject
from udl.errors import ActionFailed, Cancelled, ConfigError, NotAllowed
from udl.plugins.actions.base import ActionContext, RunLog
from udl.plugins.actions.operation import OperationAction
from udl.process import CancelToken

FINISHED = EventTypes.default().get("udl.download.finished")


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    async with with_chores(Harness(tmp_path)).service() as found:
        yield found


@pytest.fixture
def plugin(service: UDLService) -> OperationAction:
    return OperationAction(service)


def context(plugin: OperationAction, options: JsonObject) -> ActionContext[Any]:
    service = plugin.service
    return ActionContext(
        run_id=uuid4(),
        event=None,
        options=plugin.options(options),
        service=service,
        secrets=service.secrets,
        clock=service.clock,
        cancel=CancelToken(),
        log=RunLog(),
        action="call",
    )


async def run(plugin: OperationAction, options: JsonObject) -> Any:
    return await plugin.run(context(plugin, options))


async def test_it_calls_the_operation(plugin: OperationAction) -> None:
    done = await run(plugin, {"operation": "list_sites", "arguments": {"search": "x"}})

    assert done.output == {"sites": []}
    assert done.summary == "List the sites"


async def test_it_calls_a_plugin_s_operation_as_the_automations(plugin: OperationAction) -> None:
    done = await run(plugin, {"operation": "chores_do", "arguments": {"what": "dishes"}})

    assert done.output == {"what": "dishes", "caller": "automation"}


async def test_paths_it_gives_are_held_to_their_roots(
    plugin: OperationAction, tmp_path: Path
) -> None:
    arguments: JsonObject = {
        "urls": ["https://example.com/a"],
        "output_dir": str(tmp_path / "elsewhere"),
    }

    with pytest.raises(ActionFailed, match="allowed_output_roots"):
        await run(plugin, {"operation": "download", "arguments": arguments})


async def test_its_arguments_are_validated_when_it_runs(plugin: OperationAction) -> None:
    with pytest.raises(ActionFailed, match="site") as raised:
        await run(plugin, {"operation": "get_site", "arguments": {"site": ["not", "a", "key"]}})

    assert raised.value.may_retry is False


async def test_what_the_operation_raises_is_why_it_failed(plugin: OperationAction) -> None:
    with pytest.raises(ActionFailed, match=r"udl knows nothing about nowhere\.example") as missing:
        await run(plugin, {"operation": "get_site", "arguments": {"site": "nowhere.example"}})
    with pytest.raises(ActionFailed, match="connection was reset") as flaky:
        await run(plugin, {"operation": "chores_flaky"})

    assert missing.value.may_retry is False
    assert missing.value.hint is not None
    assert flaky.value.may_retry is True


async def test_cancelling_it_stops_the_operation(plugin: OperationAction) -> None:
    ctx = context(plugin, {"operation": "chores_wait"})
    running = asyncio.create_task(plugin.run(ctx))
    await asyncio.sleep(0.05)

    ctx.cancel.cancel()

    with pytest.raises(Cancelled):
        await asyncio.wait_for(running, timeout=1)


# ---------------------------------------------------------------------------------------------
# What saving one checks
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["sync_databases", "set_plugin_settings"])
async def test_an_admin_operation_is_never_called(plugin: OperationAction, operation: str) -> None:
    with pytest.raises(NotAllowed, match="admin"):
        await plugin.check({"operation": operation}, None)
    with pytest.raises(ActionFailed, match="admin"):
        await run(plugin, {"operation": operation})


async def test_an_unknown_operation_is_refused(plugin: OperationAction) -> None:
    with pytest.raises(ConfigError, match="nope") as raised:
        await plugin.check({"operation": "nope"}, None)
    with pytest.raises(ActionFailed, match="nope"):
        await run(plugin, {"operation": "nope"})

    assert raised.value.hint is not None


async def test_its_operation_is_named_plainly(plugin: OperationAction) -> None:
    with pytest.raises(ConfigError, match="template"):
        await plugin.check({"operation": "{{ event.data.op }}"}, None)


async def test_its_arguments_are_checked_with_the_event_type_s_example(
    plugin: OperationAction,
) -> None:
    good: JsonObject = {"operation": "download", "arguments": {"urls": "{{ [event.data.url] }}"}}

    await plugin.check(good, FINISHED)

    with pytest.raises(ConfigError, match="urls"):
        await plugin.check(
            {"operation": "download", "arguments": {"urls": "{{ event.data.size }}"}}, FINISHED
        )
    with pytest.raises(ConfigError, match="nope"):
        await plugin.check({"operation": "list_sites", "arguments": {"nope": 1}}, FINISHED)


async def test_without_an_example_its_plain_arguments_are_checked(
    plugin: OperationAction,
) -> None:
    await plugin.check(
        {"operation": "get_site", "arguments": {"site": "{{ event.data.site }}"}}, None
    )

    with pytest.raises(ConfigError, match="nope"):
        await plugin.check(
            {"operation": "get_site", "arguments": {"site": "{{ event.data.site }}", "nope": 1}},
            None,
        )


async def test_a_bug_in_the_operation_fails_the_run(
    plugin: OperationAction, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(ActionFailed, match="a bug in the operation"):
        await run(plugin, {"operation": "chores_broken"})

    assert "chores_broken" in caplog.text


async def test_arguments_made_whole_by_a_template_are_checked_when_it_runs(
    plugin: OperationAction,
) -> None:
    await plugin.check({"operation": "list_sites", "arguments": "{{ event.data }}"}, None)
