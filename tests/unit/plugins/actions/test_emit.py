"""
----------------
- test_emit.py -
----------------

The emit action: publishes an event of your own (custom.<name>), caused by the run and one
deeper than its event, so a chain of actions that emit what they subscribe to stops at
max_event_depth
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

# Package Imports
import pytest

# Local Imports
from support.interfaces import Harness
from udl.automations.events import EventRecord
from udl.core.service import UDLService
from udl.db.models import JsonObject
from udl.errors import ActionFailed, ConfigError
from udl.plugins.actions.base import ActionContext, RunLog
from udl.plugins.actions.emit import EmitAction
from udl.process import CancelToken


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    async with Harness(tmp_path).service() as found:
        yield found


@pytest.fixture
def plugin(service: UDLService) -> EmitAction:
    return EmitAction(service)


def context(plugin: EmitAction, options: JsonObject, depth: int = 2) -> ActionContext[Any]:
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
        depth=depth,
    )


async def test_it_publishes_its_event_caused_by_the_run(
    plugin: EmitAction, service: UDLService
) -> None:
    ctx = context(plugin, {"type": "custom.filed", "data": {"name": "x"}, "dedupe_key": "k"})

    done = await plugin.run(ctx)

    assert isinstance(done.output, dict)
    stored = await service.db.get_record(EventRecord, UUID(str(done.output["event"])))
    assert stored is not None
    assert (stored.type, stored.data, stored.dedupe_key) == ("custom.filed", {"name": "x"}, "k")
    assert (stored.caused_by, stored.depth, stored.source) == (ctx.run_id, 3, "udl")
    assert done.output["duplicate"] is False
    assert done.summary == "Emitted custom.filed"


async def test_an_event_emitted_before_is_not_again(plugin: EmitAction) -> None:
    options: JsonObject = {"type": "custom.filed", "dedupe_key": "once"}
    await plugin.run(context(plugin, options))

    again = await plugin.run(context(plugin, options))

    assert again.output == {"event": None, "duplicate": True}


async def test_a_database_that_fails_fails_the_run(plugin: EmitAction, service: UDLService) -> None:
    await service.db.close()

    with pytest.raises(ActionFailed, match="not connected"):
        await plugin.run(context(plugin, {"type": "custom.filed"}))


@pytest.mark.parametrize("named", ["udl.download.finished", "custom.", "filed", ""])
def test_it_emits_custom_events_only(plugin: EmitAction, named: str) -> None:
    with pytest.raises(ConfigError, match="custom"):
        plugin.options({"type": named})
