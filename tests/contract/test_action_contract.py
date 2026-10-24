"""
---------------------------
- test_action_contract.py -
---------------------------

The behaviour every action plugin must have: its options are validated (check() refuses bad
ones with ConfigError), its output is JSON, a cancelled run ends within a second, and nothing but
ActionFailed or Cancelled escapes a run. Each test runs once per plugin in CASES, each of which
says what good, bad, failing and waiting options are for it.
"""

# System Imports
import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

# Package Imports
import httpx
import pytest
import respx
from pydantic import JsonValue, TypeAdapter

# Local Imports
from support.actions import FakeAction, fake_notify_send, with_chores
from support.interfaces import Harness
from udl.config import AutomationsSettings
from udl.core.service import UDLService
from udl.db.models import JsonObject
from udl.errors import ActionFailed, Cancelled, ConfigError
from udl.plugins.actions.base import ActionContext, ActionPlugin, RunLog
from udl.plugins.actions.notify import NotifyAction
from udl.plugins.actions.operation import OperationAction
from udl.plugins.actions.script import ScriptAction
from udl.plugins.actions.webhook import WebhookAction
from udl.process import CancelToken

type Prepare = Callable[[Path, pytest.MonkeyPatch], contextlib.AbstractContextManager[None]]


@contextlib.contextmanager
def _nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    yield


@dataclass(frozen=True)
class Case:
    """An action plugin, and the options that make it do each thing the contract tries."""

    plugin: type[ActionPlugin[Any]]
    good: JsonObject  # it succeeds
    bad: JsonObject  # its options are not valid
    failing: JsonObject  # it fails
    waiting: JsonObject  # it runs until it is cancelled
    prepare: Prepare = _nothing  # (what the plugin needs around it: files, a server...)
    harness: Callable[[Path], Harness] = Harness  # (the udl it runs in)


# -- script ------------------------------------------------------------------------------------


@contextlib.contextmanager
def _scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    folder = tmp_path / "scripts"
    folder.mkdir()
    for name, body in [
        ("ok.sh", "echo '{\"ok\": true}'"),
        ("fail.sh", "echo 'it went wrong' >&2; exit 2"),
        ("wait.sh", "exec sleep 30"),
    ]:
        (folder / name).write_text(f"#!/bin/sh\n{body}\n")
        (folder / name).chmod(0o755)
    yield


def _with_scripts(tmp_path: Path) -> Harness:
    return Harness(tmp_path, automations=AutomationsSettings(scripts_dir=tmp_path / "scripts"))


# -- webhook -----------------------------------------------------------------------------------

HOOKS = "https://hooks.example.com"


@contextlib.contextmanager
def _hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    with respx.mock:
        respx.post(f"{HOOKS}/ok").mock(return_value=httpx.Response(200, json={"ok": True}))
        respx.post(f"{HOOKS}/fail").mock(return_value=httpx.Response(404))
        respx.post(f"{HOOKS}/wait").mock(side_effect=slow)
        yield


CASES = [
    Case(
        plugin=FakeAction,
        good={"output": {"a": [1, "two"]}, "summary": "Done"},
        bad={"outcome": "explode"},
        failing={"outcome": "fail"},
        waiting={"outcome": "wait"},
    ),
    Case(
        plugin=ScriptAction,
        good={"command": ["ok.sh"]},
        bad={"command": "ok.sh", "shell": True},
        failing={"command": ["fail.sh"]},
        waiting={"command": ["wait.sh"]},
        prepare=_scripts,
        harness=_with_scripts,
    ),
    Case(
        plugin=OperationAction,
        good={"operation": "list_sites"},
        bad={"operation": 5},
        failing={"operation": "get_site", "arguments": {"site": "nowhere.example"}},
        waiting={"operation": "chores_wait"},
        harness=lambda tmp_path: with_chores(Harness(tmp_path)),
    ),
    Case(
        plugin=WebhookAction,
        good={"url": f"{HOOKS}/ok"},
        bad={"url": "ftp://example.com/x"},
        failing={"url": f"{HOOKS}/fail"},
        waiting={"url": f"{HOOKS}/wait"},
        prepare=_hooks,
    ),
    Case(
        plugin=NotifyAction,
        good={"title": "Hello", "body": "There"},
        bad={"title": "x", "urgency": "screaming"},
        failing={"title": "fail"},
        waiting={"title": "wait"},
        prepare=fake_notify_send,
    ),
]


@pytest.fixture(params=CASES, ids=lambda case: case.plugin.name)
def case(request: pytest.FixtureRequest) -> Case:
    return request.param


@pytest.fixture
async def plugin(
    case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[ActionPlugin[Any]]:
    with case.prepare(tmp_path, monkeypatch):
        async with case.harness(tmp_path).service() as service:
            yield case.plugin(service)


def context(plugin: ActionPlugin[Any], options: JsonObject) -> ActionContext[Any]:
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
        action="contract",
    )


def test_good_options_are_valid(case: Case, plugin: ActionPlugin[Any]) -> None:
    assert isinstance(plugin.options(case.good), case.plugin.options_model)


def test_bad_options_are_a_config_error(case: Case, plugin: ActionPlugin[Any]) -> None:
    with pytest.raises(ConfigError):
        plugin.options(case.bad)


async def test_check_passes_good_options_and_refuses_bad_ones(
    case: Case, plugin: ActionPlugin[Any]
) -> None:
    await plugin.check(case.good, None)

    with pytest.raises(ConfigError):
        await plugin.check(case.bad, None)


async def test_its_output_is_json(case: Case, plugin: ActionPlugin[Any]) -> None:
    done = await plugin.run(context(plugin, case.good))

    TypeAdapter[JsonValue](JsonValue).validate_python(done.output, strict=True)
    assert isinstance(done.summary, str)


async def test_a_failure_is_an_action_failed(case: Case, plugin: ActionPlugin[Any]) -> None:
    with pytest.raises(ActionFailed) as raised:
        await plugin.run(context(plugin, case.failing))

    assert raised.value.message


async def test_a_cancelled_run_ends_within_a_second(case: Case, plugin: ActionPlugin[Any]) -> None:
    ctx = context(plugin, case.waiting)
    running = asyncio.create_task(plugin.run(ctx))
    await asyncio.sleep(0.2)
    assert not running.done()

    ctx.cancel.cancel()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        await asyncio.wait_for(running, timeout=1)

    assert time.monotonic() - started < 1
