"""
----------------------
- test_operations.py -
----------------------

Operations and the OperationRegistry: udl's own operations, what each says it does to the world
(its hints, and whether it is admin), and the operations plugins add
"""

# System Imports
from pathlib import Path
from typing import TYPE_CHECKING, cast

# Package Imports
import pytest
from pydantic import BaseModel

# Local Imports
from support.engine import registry, settings
from support.operations import Echoed, EchoRequest, Found, Gizmo, LookupRequest, echo, lookup
from udl.core.models import GetConfigRequest
from udl.core.operations import (
    APISpec,
    CLISpec,
    Operation,
    OperationHints,
    OperationMethod,
    OperationRegistry,
    operations,
    route,
)
from udl.core.service import UDLService
from udl.errors import DoesNotExist, PluginError
from udl.plugins.registry import SITES, PluginRegistry
from udl.plugins.sites.base import SitePlugin

if TYPE_CHECKING:
    from udl.core.service import UDLService as Service


# The operations the Gizmo plugin adds.
GIZMO = [
    "gizmo_echo",
    "gizmo_lookup",
    "gizmo_find",
    "gizmo_keep",
    "gizmo_caller",
    "gizmo_label",
]


def gizmo_registry(*plugins: type[SitePlugin]) -> PluginRegistry:
    found = registry()
    for plugin in plugins or (Gizmo,):
        found.register(SITES, plugin)
    return found


# ---------------------------------------------------------------------------------------------
# udl's own operations
# ---------------------------------------------------------------------------------------------


def test_every_operation_says_whether_it_is_admin() -> None:
    # admin: it changes automations, accounts, AI setup or settings, so an AI action may never
    # call it. A new operation has to be added here, choosing.
    assert {name: op.hints.admin for name, op in operations().items()} == {
        "download": False,
        "get_download": False,
        "list_downloads": False,
        "cancel_download": False,
        "probe_url": False,
        "list_sites": False,
        "get_site": False,
        "set_site_strategy": False,
        "delete_site": False,
        "suggest_chain": False,
        "test_chain": False,
        "set_site_chain": False,
        "import_chains": False,
        "export_chain": False,
        "list_databases": False,
        # (It copies what one database holds, settings included, over another's.)
        "sync_databases": True,
        "list_plugins": False,
        "get_plugin_settings": False,
        "set_plugin_settings": True,
        "get_config": False,
        # The automations': what changes them, or runs them, is admin.
        "list_event_types": False,
        "list_events": False,
        "get_event": False,
        "emit_event": True,
        "replay_event": True,
        "list_subscriptions": False,
        "get_subscription": False,
        "set_subscription": True,
        "test_subscription": False,
        "delete_subscription": True,
        "list_actions": False,
        "list_scripts": False,
        "get_action": False,
        "set_action": True,
        "run_action": True,
        "delete_action": True,
        "list_runs": False,
        "get_run": False,
        "cancel_run": True,
        "get_status": False,
        "list_schedules": False,
        "get_schedule": False,
        "set_schedule": True,
        "run_schedule": True,
        "preview_schedule": False,
        "delete_schedule": True,
    }


def test_udl_s_own_operations_have_titles_and_no_namespace() -> None:
    for op in operations().values():
        assert op.title
        assert op.namespace is None
        assert op.kind is None
        assert op.short_name == op.name


def test_a_read_only_operation_is_never_destructive() -> None:
    for op in operations().values():
        assert not (op.hints.read_only and op.hints.destructive), op.name


# ---------------------------------------------------------------------------------------------
# Operations from functions
# ---------------------------------------------------------------------------------------------


def test_an_operation_is_made_from_an_async_function_of_a_service_and_a_request() -> None:
    op = Operation.of(echo, name="echo", title="Echo", hints=OperationHints(read_only=True))

    assert (op.name, op.title, op.request, op.response) == ("echo", "Echo", EchoRequest, Echoed)
    assert op.summary == "Echo a request back: every field it was given, as udl understood it."
    assert op.description.startswith(op.summary)
    assert op.description.endswith("For testing the adapters udl makes for a plugin's operations.")
    assert op.hints == OperationHints(read_only=True)
    assert op.method is echo


def test_an_operation_needs_a_docstring() -> None:
    async def undocumented(service: Service, request: LookupRequest) -> Found:
        raise AssertionError

    with pytest.raises(PluginError, match="undocumented"):
        Operation.of(undocumented, name="x", title="X")


def test_an_operation_takes_and_gives_models() -> None:
    async def plain(service: Service, request: str) -> Found:
        """Not a model."""
        raise AssertionError

    async def answers_plain(service: Service, request: LookupRequest) -> str:
        """Gives no model."""
        raise AssertionError

    with pytest.raises(PluginError, match="plain"):
        Operation.of(plain, name="x", title="X")
    with pytest.raises(PluginError, match="answers_plain"):
        Operation.of(cast(OperationMethod, answers_plain), name="x", title="X")


@pytest.mark.parametrize("name", ["", "Echo", "two words", "-x", "a/b"])
def test_an_operation_s_name_is_a_lowercase_word(name: str) -> None:
    with pytest.raises(PluginError, match="name"):
        Operation.of(echo, name=name, title="Echo")


def test_an_operation_in_a_namespace_is_named_after_it() -> None:
    op = Operation.of(echo, name="echo", title="Echo").in_namespace("gizmo", kind=SITES.group)

    assert (op.name, op.short_name, op.namespace, op.kind) == (
        "gizmo_echo",
        "echo",
        "gizmo",
        "udl.sites",
    )


@pytest.mark.parametrize(
    ("specs", "named"),
    [
        ({"cli": CLISpec(arguments=("nope",))}, "nope"),
        ({"cli": CLISpec(options={"nope": "--x"})}, "nope"),
        ({"cli": CLISpec(path=())}, "path"),
        ({"cli": CLISpec(pairs=("text",))}, "text"),  # (pairs make a dict, which text is not)
        ({"cli": CLISpec(pairs=("nope",))}, "nope"),
        ({"cli": CLISpec(arguments=("token",))}, "token"),  # (a secret is never an argument)
        ({"api": APISpec(path="/things/{nope}")}, "nope"),
        ({"api": APISpec(method="TRACE")}, "TRACE"),
    ],
)
def test_an_operation_s_specs_name_its_fields(specs: dict[str, object], named: str) -> None:
    with pytest.raises(PluginError, match=named):
        Operation.of(echo, name="echo", title="Echo", **specs)  # pyright: ignore[reportArgumentType]


def test_a_plugin_s_operation_is_routed_by_its_kind() -> None:
    op = Operation.of(echo, name="echo", title="Echo")

    assert route(op.in_namespace("gizmo", kind=SITES.group)) == APISpec(
        method="POST", path="/plugins/gizmo/echo"
    )
    assert route(op.in_namespace("gmail", kind="udl.integrations")).path == (
        "/integrations/gmail/echo"
    )
    assert (
        route(Operation.of(echo, name="echo", title="Echo", api=APISpec(path="/x/{text}"))).path
        == "/x/{text}"
    )


def test_pairs_may_make_an_optional_dict() -> None:
    class Tagged(BaseModel):
        tags: dict[str, str] | None = None

    async def tag(service: Service, request: Tagged) -> Found:
        """Tag."""
        raise AssertionError

    op = Operation.of(tag, name="tag", title="Tag", cli=CLISpec(pairs=("tags",)))

    assert op.cli is not None


def test_a_plugin_s_command_is_in_its_own_group() -> None:
    # (A one-word command is one of udl's own, such as `udl status`.)
    op = Operation.of(echo, name="echo", title="Echo", cli=CLISpec(path=("echo",)))

    with pytest.raises(PluginError, match="two words"):
        op.in_namespace("gizmo", kind=SITES.group)


def test_a_plugin_operation_called_settings_is_refused_unless_an_integration_s() -> None:
    class Settled(SitePlugin):
        name = "settled"

        @classmethod
        def operations(cls) -> list[Operation]:
            return [Operation.of(echo, name="settings", title="Echo")]

    plugins = gizmo_registry(Settled)

    ops = OperationRegistry.discover(plugins)

    assert "settled_settings" not in ops
    [problem] = plugins.problems
    assert "settings" in problem.error


# ---------------------------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------------------------


def test_the_core_registry_holds_udl_s_own_operations_in_order() -> None:
    ops = OperationRegistry.core()

    assert ops.names() == list(operations())
    assert ops.get("download") is operations()["download"]
    assert "download" in ops
    assert len(ops) == len(operations())


def test_an_unknown_operation_does_not_exist() -> None:
    with pytest.raises(DoesNotExist, match="nope") as raised:
        OperationRegistry.core().get("nope")

    assert raised.value.hint is not None
    assert "download" in raised.value.hint


def test_an_operation_named_like_another_is_refused() -> None:
    ops = OperationRegistry.core()
    clash = Operation.of(echo, name="download", title="Echo")

    with pytest.raises(PluginError, match="download"):
        ops.add(clash)

    assert ops.get("download") is operations()["download"]


def test_operations_are_listed_by_namespace() -> None:
    ops = OperationRegistry.core()
    for op in Gizmo.operations():
        ops.add(op.in_namespace("gizmo", kind=SITES.group))

    assert ops.namespaces() == ["gizmo"]
    assert [op.name for op in ops.in_namespace("gizmo")] == GIZMO
    assert [op.name for op in ops.in_namespace(None)] == list(operations())
    assert ops.in_namespace("nope") == []


def test_a_registry_is_discovered_with_every_plugin_s_operations() -> None:
    ops = OperationRegistry.discover(gizmo_registry())

    assert ops.names() == [*operations(), *GIZMO]
    assert ops.get("gizmo_echo").kind == "udl.sites"


def test_a_plugin_operation_named_like_another_is_reported_and_left_out() -> None:
    class Listing(SitePlugin):
        name = "list"

        @classmethod
        def operations(cls) -> list[Operation]:
            # list + sites = list_sites: udl's own.
            return [
                Operation.of(echo, name="sites", title="Echo"),
                Operation.of(lookup, name="things", title="Look up"),
            ]

    plugins = gizmo_registry(Gizmo, Listing)

    ops = OperationRegistry.discover(plugins)

    assert ops.get("list_sites") is operations()["list_sites"]
    assert "list_things" in ops
    [problem] = plugins.problems
    assert (problem.kind, problem.origin) == ("udl.sites", plugins.plugins(SITES)[1].origin)
    assert "list_sites" in problem.error


def test_what_is_not_an_operation_is_reported_and_left_out() -> None:
    class Odd(SitePlugin):
        name = "odd"

        @classmethod
        def operations(cls) -> list[object]:
            return ["echo", Operation.of(echo, name="echo", title="Echo")]

    plugins = gizmo_registry(Odd)

    ops = OperationRegistry.discover(plugins)

    assert "odd_echo" in ops
    [problem] = plugins.problems
    assert "'echo'" in problem.error


def test_a_plugin_whose_name_cannot_name_operations_is_reported() -> None:
    class Shouty(SitePlugin):
        name = "Shouty Plugin"

        @classmethod
        def operations(cls) -> list[Operation]:
            return [Operation.of(echo, name="echo", title="Echo")]

    plugins = gizmo_registry(Gizmo, Shouty)

    ops = OperationRegistry.discover(plugins)

    assert ops.namespaces() == ["gizmo"]
    [problem] = plugins.problems
    assert "'Shouty Plugin'" in problem.error


def test_a_plugin_whose_operations_fail_is_reported_and_the_others_kept() -> None:
    class Broken(SitePlugin):
        name = "broken"

        @classmethod
        def operations(cls) -> list[Operation]:
            raise RuntimeError("no operations today")

    plugins = gizmo_registry(Gizmo, Broken)

    ops = OperationRegistry.discover(plugins)

    assert ops.namespaces() == ["gizmo"]
    [problem] = plugins.problems
    assert "no operations today" in problem.error


# ---------------------------------------------------------------------------------------------
# A service's operations
# ---------------------------------------------------------------------------------------------


def make_service(tmp_path: Path) -> UDLService:
    return UDLService(settings(tmp_path), registry=gizmo_registry())


def test_a_service_s_operations_are_udl_s_and_its_plugins(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    assert service.operations.names() == [*operations(), *GIZMO]


async def test_a_plugin_s_operation_is_called_like_udl_s(tmp_path: Path) -> None:
    async with make_service(tmp_path) as service:
        echoed = await service.call("gizmo_echo", {"text": "hi", "tags": ["a"]})
        config = await service.call("get_config", GetConfigRequest())
        with pytest.raises(DoesNotExist, match="nope") as raised:
            await service.call("nope", {})

    assert raised.value.hint is not None
    assert "gizmo_echo" in raised.value.hint

    assert isinstance(echoed, Echoed)
    assert (echoed.text, echoed.tags) == ("hi", ["a"])
    assert isinstance(config, BaseModel)
