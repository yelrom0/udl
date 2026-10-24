"""
------------------
- test_notify.py -
------------------

The notify action: a desktop notification, through notify-send (a fake one on PATH here), whose
title and body are always text to it, never its options (they come after `--`)
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
from support.actions import fake_notify_send
from support.interfaces import Harness
from udl.core.service import UDLService
from udl.db.models import JsonObject
from udl.errors import ActionFailed, Cancelled, ConfigError
from udl.plugins.actions.base import ActionContext, RunLog
from udl.plugins.actions.notify import NotifyAction
from udl.plugins.requirements import Requirement
from udl.process import CancelToken


@pytest.fixture
async def plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[NotifyAction]:
    with fake_notify_send(tmp_path, monkeypatch):
        async with Harness(tmp_path).service() as service:
            yield NotifyAction(service)


def context(plugin: NotifyAction, options: JsonObject) -> ActionContext[Any]:
    service: UDLService = plugin.service
    return ActionContext(
        run_id=uuid4(),
        event=None,
        options=plugin.options(options),
        service=service,
        secrets=service.secrets,
        clock=service.clock,
        cancel=CancelToken(),
        log=RunLog(),
    )


def notified(tmp_path: Path) -> list[str]:
    return (tmp_path / "notified").read_text().splitlines()


async def test_it_shows_a_notification(plugin: NotifyAction, tmp_path: Path) -> None:
    done = await plugin.run(context(plugin, {"title": "Mail", "body": "From the bank"}))

    assert notified(tmp_path) == [
        "--app-name=udl",
        "--urgency=normal",
        "--",
        "Mail",
        "From the bank",
    ]
    assert done.summary == "Mail"


async def test_its_urgency_is_its_own(plugin: NotifyAction, tmp_path: Path) -> None:
    await plugin.run(context(plugin, {"title": "Now", "urgency": "critical"}))

    assert "--urgency=critical" in notified(tmp_path)


async def test_its_title_is_never_an_option(plugin: NotifyAction, tmp_path: Path) -> None:
    await plugin.run(context(plugin, {"title": "--help", "body": "-v"}))

    assert notified(tmp_path)[-3:] == ["--", "--help", "-v"]


async def test_a_notification_that_fails_says_why(plugin: NotifyAction) -> None:
    with pytest.raises(ActionFailed, match="Cannot connect to the notification server"):
        await plugin.run(context(plugin, {"title": "fail"}))


async def test_cancelling_it_stops_notify_send(plugin: NotifyAction) -> None:
    ctx = context(plugin, {"title": "wait"})
    running = asyncio.create_task(plugin.run(ctx))
    await asyncio.sleep(0.2)

    ctx.cancel.cancel()

    with pytest.raises(Cancelled):
        await asyncio.wait_for(running, timeout=1)


async def test_without_notify_send_it_fails(
    plugin: NotifyAction, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))

    with pytest.raises(ActionFailed, match="notify-send"):
        await plugin.run(context(plugin, {"title": "x"}))


def test_it_needs_notify_send() -> None:
    assert [requirement.name for requirement in NotifyAction.requires] == ["notify-send"]
    assert NotifyAction.requires[0].kind == Requirement.binary("notify-send").kind


@pytest.mark.parametrize("options", [{"title": ""}, {"title": "x", "urgency": "screaming"}, {}])
def test_its_options_are_checked(plugin: NotifyAction, options: JsonObject) -> None:
    with pytest.raises(ConfigError):
        plugin.options(options)
