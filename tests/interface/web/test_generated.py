"""
------------------
- test_generated.py -
------------------

GET /ui/generated.json: everything the shell generates its pages from — each plugin's operations
(with the view it opens in, what it does, its route and both schemas), its settings form, how its
accounts connect and the event types it publishes — and udl's own operations under the pseudo-
plugin `udl`. Interface-only (it is not an operation), behind the API's key, revalidated by ETag.
"""

# System Imports
import json
from pathlib import Path
from typing import Final

# Package Imports
from pydantic import SecretStr

# Local Imports
from support.actions import with_chores
from support.api import client_for
from support.integrations import FakeIntegration
from support.interfaces import Harness
from udl.config import APISettings
from udl.core.operations import operations, resolved_view, route
from udl.interfaces.web.generated import UDL, GeneratedPlugin, GeneratedUI, generated
from udl.plugins.registry import INTEGRATIONS, SITES
from udl.plugins.ui.base import UIBundle

MANIFEST: Final = {"name": "chores", "version": "0.1.0", "sdk": "^1.0", "entry": "index.js"}


def ui_of(harness: Harness) -> GeneratedUI:
    """The generated UI, for `harness`'s service."""
    service = harness.service()
    return generated(service.operations, harness.registry)


def by_name(found: GeneratedUI) -> dict[str, GeneratedPlugin]:
    """The plugins, by name."""
    return {plugin.name: plugin for plugin in found.plugins}


def a_bundle(tmp_path: Path) -> UIBundle:
    """A UI plugin's bundle, for the plugin that brings one."""
    folder = tmp_path / "ui-plugins" / "chores"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps(MANIFEST))
    (folder / "index.js").write_text("export default {}\n")
    return UIBundle.of(folder)


# ---------------------------------------------------------------------------------------------
# udl's own operations
# ---------------------------------------------------------------------------------------------


def test_udl_s_own_operations_are_under_a_pseudo_plugin(tmp_path: Path) -> None:
    found = by_name(ui_of(Harness(tmp_path)))[UDL]

    assert {op.name for op in found.operations} == set(operations())


def test_udl_s_own_operations_have_no_route_of_their_own(tmp_path: Path) -> None:
    # (Their routes are hand-written in udl.interfaces.api.routes, so route() cannot say them:
    # the shell calls them by name, through the typed client.)
    first = by_name(ui_of(Harness(tmp_path)))[UDL].operations[0]

    assert (first.method, first.path) == (None, None)


def test_udl_is_the_first_plugin_listed(tmp_path: Path) -> None:
    found = ui_of(with_chores(Harness(tmp_path)))

    assert found.plugins[0].name == UDL


# ---------------------------------------------------------------------------------------------
# A plugin's operations
# ---------------------------------------------------------------------------------------------


def test_a_plugins_operation_carries_its_route_view_hints_and_schemas(tmp_path: Path) -> None:
    harness = with_chores(Harness(tmp_path))
    op = harness.service().operations.get("chores_do")

    [entry] = [
        found for found in by_name(ui_of(harness))["chores"].operations if found.name == op.name
    ]

    spec = route(op)
    assert (entry.method, entry.path) == (spec.method, spec.path)
    assert entry.ui.view == resolved_view(op)
    assert entry.hints == op.hints
    assert isinstance(entry.request_schema, dict)
    assert entry.request_schema["type"] == "object"
    assert isinstance(entry.response_schema, dict)
    assert entry.response_schema["type"] == "object"


def test_every_view_is_the_one_it_opens_in(tmp_path: Path) -> None:
    # (auto is resolved here: the shell never has to know what auto means.)
    found = ui_of(with_chores(Harness(tmp_path)))

    views = {op.ui.view for plugin in found.plugins for op in plugin.operations}

    assert views <= {"list", "detail", "form", "action"}


def test_the_schemas_are_self_contained(tmp_path: Path) -> None:
    # (The shell draws a form or a table from one schema. A $ref is only ever left in a schema
    # that cannot be inlined — a definition that refers to itself, a filter tree — and then its
    # $defs are kept with it, so it still resolves on its own.)
    found = ui_of(with_chores(Harness(tmp_path)))

    for plugin in found.plugins:
        for op in plugin.operations:
            for schema in (op.request_schema, op.response_schema):
                text = json.dumps(schema)
                assert ("$ref" in text) == (isinstance(schema, dict) and "$defs" in schema)


# ---------------------------------------------------------------------------------------------
# Settings, accounts and event types
# ---------------------------------------------------------------------------------------------


def test_a_plugin_with_settings_has_a_form(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.registry.register(INTEGRATIONS, FakeIntegration)

    form = by_name(ui_of(harness))["fake"].settings

    assert form is not None
    assert isinstance(form.json_schema, dict)
    assert form.json_schema["type"] == "object"


def test_the_form_says_which_fields_are_secrets(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.registry.register(INTEGRATIONS, FakeIntegration)

    form = by_name(ui_of(harness))["fake"].settings

    assert form is not None
    assert form.secret == ["client_secret"]


def test_the_form_says_which_fields_are_the_config_files(tmp_path: Path) -> None:
    # (None of the fake integration's are: one that has them is 18.7's settings test.)
    harness = Harness(tmp_path)
    harness.registry.register(INTEGRATIONS, FakeIntegration)

    form = by_name(ui_of(harness))["fake"].settings

    assert form is not None
    assert form.read_only == []


def test_a_plugin_without_settings_has_no_form(tmp_path: Path) -> None:
    found = by_name(ui_of(with_chores(Harness(tmp_path))))

    assert found["chores"].settings is None


def test_an_integration_says_how_its_accounts_connect(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.registry.register(INTEGRATIONS, FakeIntegration)

    accounts = by_name(ui_of(harness))["fake"].accounts

    assert accounts is not None
    assert accounts.connect == [method.value for method in FakeIntegration.connect]
    assert accounts.access_levels == list(FakeIntegration.access_levels)
    assert isinstance(accounts.options_schema, dict)
    assert accounts.options_schema["type"] == "object"


def test_a_plugin_that_is_not_an_integration_has_no_accounts(tmp_path: Path) -> None:
    found = by_name(ui_of(with_chores(Harness(tmp_path))))

    assert found["chores"].accounts is None


def test_an_integrations_event_types_are_listed(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.registry.register(INTEGRATIONS, FakeIntegration)

    found = by_name(ui_of(harness))["fake"]

    assert found.event_types == [kind.name for kind in FakeIntegration.event_types()]


def test_a_plugin_that_publishes_no_events_lists_none(tmp_path: Path) -> None:
    found = by_name(ui_of(with_chores(Harness(tmp_path))))

    assert found["chores"].event_types == []


# ---------------------------------------------------------------------------------------------
# The UI plugin flag
# ---------------------------------------------------------------------------------------------


def test_a_plugin_that_brings_a_ui_says_so(tmp_path: Path) -> None:
    harness = with_chores(Harness(tmp_path))
    chores = harness.registry.get(SITES, "chores")
    harness.registry.register(SITES, type("Chores", (chores,), {"ui": a_bundle(tmp_path)}))

    found = by_name(ui_of(harness))

    assert found["chores"].has_ui_plugin is True


def test_a_plugin_without_a_ui_says_it_has_none(tmp_path: Path) -> None:
    found = by_name(ui_of(with_chores(Harness(tmp_path))))

    assert found["chores"].has_ui_plugin is False


# ---------------------------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------------------------


async def test_the_route_answers_with_the_json(tmp_path: Path) -> None:
    harness = with_chores(Harness(tmp_path))

    async with client_for(harness) as client:
        answer = await client.get("/ui/generated.json")

    assert answer.status_code == 200
    assert answer.json()["plugins"][0]["name"] == UDL
    assert answer.headers["Cache-Control"] == "no-cache"
    assert answer.headers["ETag"]


async def test_the_route_is_not_sent_again_when_it_has_not_changed(tmp_path: Path) -> None:
    harness = with_chores(Harness(tmp_path))

    async with client_for(harness) as client:
        first = await client.get("/ui/generated.json")
        again = await client.get(
            "/ui/generated.json", headers={"If-None-Match": first.headers["ETag"]}
        )

    assert again.status_code == 304
    assert again.content == b""


async def test_the_route_needs_the_api_key_when_there_is_one(tmp_path: Path) -> None:
    harness = Harness(tmp_path, api=APISettings(api_key=SecretStr("a-key")))

    async with client_for(harness) as client:
        refused = await client.get("/ui/generated.json")
        allowed = await client.get("/ui/generated.json", headers={"X-API-Key": "a-key"})

    assert refused.status_code == 401
    assert allowed.status_code == 200


async def test_the_shell_s_files_still_need_no_key(tmp_path: Path) -> None:
    # (Only the generated JSON says what the plugins do: the build holds no data.)
    harness = Harness(tmp_path, api=APISettings(api_key=SecretStr("a-key")))

    async with client_for(harness) as client:
        answer = await client.get("/ui/plugins.json")

    assert answer.status_code == 200


def test_the_json_is_the_same_twice(tmp_path: Path) -> None:
    # (The shell caches it by its ETag: two generations of the same udl are the same JSON.)
    harness = with_chores(Harness(tmp_path))

    assert ui_of(harness) == ui_of(harness)
