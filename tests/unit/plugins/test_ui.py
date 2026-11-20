"""
-------------
- test_ui.py -
-------------

UI plugins (udl.plugins.ui): a manifest is checked against the closed set of contribution
points, its SDK range, and that its entry and styles are files inside the bundle; a bundle that
fails is a problem, never served. Bundles come from a plugin's `ui`, the `udl.ui` kind's entry
points and `[web] ui_plugin_dirs`, and `GET /ui/plugins.json` lists the valid ones.
"""

# System Imports
import json
from pathlib import Path
from typing import Any, Final

# Package Imports
import pytest

# Local Imports
from support.actions import with_chores
from support.api import client_for
from support.engine import settings
from support.interfaces import Harness
from udl.config import WebSettings
from udl.errors import PluginError
from udl.plugins.registry import UI, PluginRegistry
from udl.plugins.ui import (
    SDK_VERSION,
    GeneratedNav,
    UIBundle,
    UIManifest,
    UIPlugin,
    guidance_problems,
)
from udl.plugins.ui.discovery import ui_plugins

MANIFEST: Final[dict[str, Any]] = {
    "name": "gmail",
    "version": "0.1.0",
    "sdk": "^1.0",
    "entry": "index.js",
    "styles": ["index.css"],
    "contributes": {"nav": [{"title": "Gmail", "icon": "mail", "route": "inbox"}]},
}


def bundle(tmp_path: Path, **changes: Any) -> Path:
    """A UI plugin's bundle: a folder with a manifest.json, an entry and a stylesheet."""
    folder = tmp_path / "ui-plugins" / "gmail"
    folder.mkdir(parents=True)
    manifest = {**MANIFEST, **changes}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    (folder / "index.js").write_text("export default {}\n")
    (folder / "index.css").write_text(".gmail { color: red }\n")
    return folder


def of(folder: Path) -> UIBundle:
    return UIBundle.of(folder)


# ---------------------------------------------------------------------------------------------
# A manifest
# ---------------------------------------------------------------------------------------------


def test_a_manifest_is_read_with_its_contribution_points(tmp_path: Path) -> None:
    found = of(bundle(tmp_path))

    assert found.manifest.name == "gmail"
    assert found.manifest.isolation == "shadow"  # (the default)
    assert [(item.title, item.route) for item in found.manifest.contributes.nav] == [
        ("Gmail", "inbox")
    ]


def test_the_points_are_the_manifest_s_and_a_closed_set(tmp_path: Path) -> None:
    found = of(
        bundle(
            tmp_path,
            contributes={
                "routes": ["inbox", "message/$id"],
                "settings": ["gmail"],
                "accountSetup": ["gmail"],
                "eventViews": ["gmail.*"],
                "filterEditors": ["gmail"],
                "actionEditors": [],
                "widgets": [{"slot": "dashboard", "id": "unread"}],
                "themes": ["gmail-dark"],
                "operationViews": ["gmail_list"],
                "resultViews": ["gmail_*"],
                "fieldWidgets": ["rating"],
            },
        )
    )

    assert found.manifest.contributes.event_views == ["gmail.*"]
    assert found.manifest.contributes.account_setup == ["gmail"]
    assert [widget.id for widget in found.manifest.contributes.widgets] == ["unread"]
    # (24.7's three: what replaces a generated panel, a result, and a field.)
    assert found.manifest.contributes.operation_views == ["gmail_list"]
    assert found.manifest.contributes.result_views == ["gmail_*"]
    assert found.manifest.contributes.field_widgets == ["rating"]


def test_a_point_the_shell_does_not_know_is_a_problem(tmp_path: Path) -> None:
    folder = bundle(tmp_path, contributes={"sidebar": ["x"]})

    with pytest.raises(PluginError, match="sidebar"):
        of(folder)


@pytest.mark.parametrize("sdk", ["^2.0", "^1.2", "1.0", ">=1.0", "^0.9"])
def test_an_sdk_range_the_shell_does_not_have_is_a_problem(tmp_path: Path, sdk: str) -> None:
    folder = bundle(tmp_path, sdk=sdk)

    with pytest.raises(PluginError, match="sdk"):
        of(folder)


@pytest.mark.parametrize("sdk", ["^1.0", "^1.0.0", "^1.0.7", "^1.1"])
def test_an_sdk_range_the_shell_has_is_taken(tmp_path: Path, sdk: str) -> None:
    assert of(bundle(tmp_path, sdk=sdk)).manifest.sdk == sdk


@pytest.mark.parametrize("entry", ["../outside.js", "nothere.js", "/etc/passwd"])
def test_an_entry_outside_the_bundle_is_a_problem(tmp_path: Path, entry: str) -> None:
    folder = bundle(tmp_path, entry=entry)

    with pytest.raises(PluginError, match="entry"):
        of(folder)


def test_a_stylesheet_outside_the_bundle_is_a_problem(tmp_path: Path) -> None:
    folder = bundle(tmp_path, styles=["../index.css"])

    with pytest.raises(PluginError, match="style"):
        of(folder)


def test_a_folder_without_a_manifest_is_not_a_bundle(tmp_path: Path) -> None:
    folder = tmp_path / "ui-plugins" / "gmail"
    folder.mkdir(parents=True)

    with pytest.raises(PluginError, match="manifest"):
        of(folder)


def test_a_manifest_that_is_not_json_is_a_problem(tmp_path: Path) -> None:
    folder = bundle(tmp_path)
    (folder / "manifest.json").write_text("{not json")

    with pytest.raises(PluginError, match="manifest"):
        of(folder)


def test_an_isolation_the_shell_does_not_do_is_a_problem(tmp_path: Path) -> None:
    with pytest.raises(PluginError, match="isolation"):
        of(bundle(tmp_path, isolation="iframe"))


# ---------------------------------------------------------------------------------------------
# How it guides the page udl generates (24.7)
# ---------------------------------------------------------------------------------------------


def test_guidance_is_read_whole(tmp_path: Path) -> None:
    found = of(
        bundle(
            tmp_path,
            generated={
                "nav": {"title": "Mail", "icon": "mail"},
                "tabs": ["accounts", "operations"],
                "hide": ["gmail_send_*"],
                "groups": {"Mail": ["gmail_list"]},
                "order": ["gmail_list"],
                "labels": {"gmail_list": "Inbox"},
            },
        )
    )

    guidance = found.manifest.generated
    assert guidance is not None
    assert isinstance(guidance.nav, GeneratedNav)
    assert (guidance.nav.title, guidance.nav.icon) == ("Mail", "mail")
    assert guidance.tabs == ["accounts", "operations"]
    assert guidance.hide == ["gmail_send_*"]
    assert guidance.groups == {"Mail": ["gmail_list"]}
    assert guidance.order == ["gmail_list"]
    assert guidance.labels == {"gmail_list": "Inbox"}


def test_a_manifest_without_guidance_has_none(tmp_path: Path) -> None:
    assert of(bundle(tmp_path)).manifest.generated is None


def test_guidance_can_turn_the_drawer_s_entry_off(tmp_path: Path) -> None:
    # (A UI plugin that brings no nav of its own, and whose page should not be in the drawer.)
    found = of(bundle(tmp_path, generated={"nav": False}))

    assert found.manifest.generated is not None
    assert found.manifest.generated.nav is False


def test_a_tab_the_page_does_not_have_is_a_problem(tmp_path: Path) -> None:
    with pytest.raises(PluginError, match="tabs"):
        of(bundle(tmp_path, generated={"tabs": ["inbox"]}))


def test_a_guidance_field_the_shell_does_not_know_is_a_problem(tmp_path: Path) -> None:
    with pytest.raises(PluginError, match="generated"):
        of(bundle(tmp_path, generated={"sidebar": True}))


def test_guidance_that_names_nothing_is_a_problem_not_an_error(tmp_path: Path) -> None:
    # (A typo in guidance is said, not refused: the bundle is still served, and the page ignores
    # what matches no operation.)
    found = of(
        bundle(
            tmp_path,
            generated={
                "hide": ["gmail_send_*"],
                "labels": {"gmail_sned": "Sent"},
                "groups": {"Mail": ["gmail_sned"]},
                "order": ["gmail_sned"],
            },
            contributes={"operationViews": ["gmail_sned"], "resultViews": ["gmail_sned"]},
        )
    )

    assert guidance_problems(found.manifest, ["gmail_list", "gmail_send"]) == [
        'its guidance hides "gmail_send_*", and no operation matches',
        'its guidance labels "gmail_sned", and it has no operation of that name',
        'its guidance groups "gmail_sned", and it has no operation of that name',
        'its guidance orders "gmail_sned", and it has no operation of that name',
        'its operationViews names "gmail_sned", and it has no operation of that name',
        'its resultViews names "gmail_sned", and it has no operation of that name',
    ]


def test_guidance_that_names_an_operation_is_no_problem(tmp_path: Path) -> None:
    found = of(
        bundle(
            tmp_path,
            generated={
                "hide": ["gmail_send", "gmail_send_*"],
                "labels": {"gmail_list": "Inbox"},
                "groups": {"Mail": ["gmail_list"]},
                "order": ["gmail_list"],
            },
            contributes={"operationViews": ["gmail_list"], "resultViews": ["gmail_send_*"]},
        )
    )

    # (A `*` is a prefix: `gmail_send_*` names `gmail_send_all`, and `gmail_send` itself is named
    # by its own name.)
    assert guidance_problems(found.manifest, ["gmail_list", "gmail_send", "gmail_send_all"]) == []


def test_a_manifest_without_guidance_has_no_problems(tmp_path: Path) -> None:
    assert guidance_problems(of(bundle(tmp_path)).manifest, []) == []


# ---------------------------------------------------------------------------------------------
# Where bundles come from
# ---------------------------------------------------------------------------------------------


class Mailbox(UIPlugin):
    """A UI-only plugin (a `udl.ui` entry point), with no Python behaviour of its own."""

    name = "mailbox"


def test_a_folder_in_ui_plugin_dirs_is_listed(tmp_path: Path) -> None:
    found = ui_plugins(
        settings(tmp_path, web=WebSettings(ui_plugin_dirs=[bundle(tmp_path).parent])),
        PluginRegistry(),
    )

    assert [(plugin.name, plugin.plugin) for plugin in found.plugins] == [("gmail", None)]
    assert found.plugins[0].base_url == "/ui/plugins/gmail/"
    assert found.plugins[0].entry_url == "/ui/plugins/gmail/index.js"
    assert found.plugins[0].styles == ["/ui/plugins/gmail/index.css"]
    assert found.problems == []


def test_a_folder_that_is_not_a_bundle_is_not_one(tmp_path: Path) -> None:
    # (A folder of files is not a UI plugin: the manifest is what makes it one, so no problem.)
    folder = tmp_path / "ui-plugins"
    (folder / "half").mkdir(parents=True)
    (folder / "half" / "index.js").write_text("export default {}\n")

    found = ui_plugins(
        settings(tmp_path, web=WebSettings(ui_plugin_dirs=[folder])), PluginRegistry()
    )

    assert (found.plugins, found.problems) == ([], [])


def test_a_broken_manifest_is_a_problem(tmp_path: Path) -> None:
    folder = tmp_path / "ui-plugins"
    (folder / "half").mkdir(parents=True)
    (folder / "half" / "manifest.json").write_text("{}")

    found = ui_plugins(
        settings(tmp_path, web=WebSettings(ui_plugin_dirs=[folder])), PluginRegistry()
    )

    assert found.plugins == []
    assert [problem.origin for problem in found.problems] == [str(folder / "half")]


def test_a_plugin_of_any_kind_brings_its_own_ui(tmp_path: Path) -> None:
    plugins = PluginRegistry()
    plugins.register(UI, _as_ui_plugin("gmail", of(bundle(tmp_path))))

    found = ui_plugins(settings(tmp_path, web=WebSettings(ui_plugin_dirs=[])), plugins)

    assert [(plugin.name, plugin.plugin) for plugin in found.plugins] == [("gmail", None)]


def _as_ui_plugin(name: str, found: UIBundle) -> type[UIPlugin]:
    """A UIPlugin class called `name` with `found` as its bundle."""
    return type(name.title(), (UIPlugin,), {"name": name, "ui": found})


def test_a_ui_only_plugin_is_named_after_its_manifest(tmp_path: Path) -> None:
    plugins = PluginRegistry()
    plugins.register(UI, _as_ui_plugin("mailbox", of(bundle(tmp_path, name="mailbox"))))

    found = ui_plugins(settings(tmp_path, web=WebSettings(ui_plugin_dirs=[])), plugins)

    assert [plugin.name for plugin in found.plugins] == ["mailbox"]


async def test_a_ui_only_bundle_s_guidance_is_checked_against_every_operation(
    tmp_path: Path,
) -> None:
    """Its guidance reaches every page, so it is checked against every operation udl knows: a
    hide that names another plugin's operation is no problem, one that names nothing is said on
    the bundle (which is still served)."""
    harness = with_chores(
        Harness(
            tmp_path,
            web=WebSettings(
                ui_plugin_dirs=[
                    bundle(tmp_path, name="mailbox", generated={"hide": ["chores_nope"]}).parent
                ]
            ),
        )
    )

    async with client_for(harness) as client:
        answer = await client.get("/ui/plugins.json")

    [plugin] = answer.json()["plugins"]
    assert plugin["problems"] == ['its guidance hides "chores_nope", and no operation matches']


async def test_a_ui_only_bundle_that_hides_another_plugin_s_operation_is_no_problem(
    tmp_path: Path,
) -> None:
    harness = with_chores(
        Harness(
            tmp_path,
            web=WebSettings(
                ui_plugin_dirs=[
                    bundle(tmp_path, name="mailbox", generated={"hide": ["chores_do"]}).parent
                ]
            ),
        )
    )

    async with client_for(harness) as client:
        answer = await client.get("/ui/plugins.json")

    [plugin] = answer.json()["plugins"]
    assert plugin["problems"] == []


# ---------------------------------------------------------------------------------------------
# The list, and the plugins that have one
# ---------------------------------------------------------------------------------------------


async def test_the_api_lists_the_ui_plugins(tmp_path: Path) -> None:
    harness = Harness(tmp_path, web=WebSettings(ui_plugin_dirs=[bundle(tmp_path).parent]))

    async with client_for(harness) as client:
        answer = await client.get("/ui/plugins.json")

    assert answer.status_code == 200
    [plugin] = answer.json()["plugins"]
    assert (plugin["name"], plugin["entry_url"]) == ("gmail", "/ui/plugins/gmail/index.js")
    assert answer.json()["problems"] == []


async def test_the_api_lists_the_guidance_a_bundle_gives(tmp_path: Path) -> None:
    # (The shell reads the guidance from here, beside the page udl generates.)
    folder = bundle(tmp_path, generated={"nav": False, "tabs": ["operations"]})
    harness = Harness(tmp_path, web=WebSettings(ui_plugin_dirs=[folder.parent]))

    async with client_for(harness) as client:
        answer = await client.get("/ui/plugins.json")

    guidance = answer.json()["plugins"][0]["generated"]
    assert guidance["nav"] is False
    assert guidance["tabs"] == ["operations"]
    assert (guidance["hide"], guidance["labels"]) == ([], {})


async def test_a_plugins_bundle_is_served(tmp_path: Path) -> None:
    """A bundle a plugin brings is served at its URL, like a folder's: its files are the
    plugin's, and the shell asks for them where /ui/plugins.json said they were."""
    harness = Harness(tmp_path, web=WebSettings(ui_plugin_dirs=[]))
    harness.registry.register(UI, _as_ui_plugin("mailbox", of(bundle(tmp_path, name="mailbox"))))

    async with client_for(harness) as client:
        listed = await client.get("/ui/plugins.json")
        entry = await client.get("/ui/plugins/mailbox/index.js")
        style = await client.get("/ui/plugins/mailbox/index.css")

    assert [plugin["name"] for plugin in listed.json()["plugins"]] == ["mailbox"]
    assert (entry.status_code, entry.text) == (200, "export default {}\n")
    assert (style.status_code, style.text) == (200, ".gmail { color: red }\n")


async def test_a_name_two_bundles_take_is_a_problem_and_the_plugin_wins(tmp_path: Path) -> None:
    """Two bundles with one name are one plugin: the first found is listed and served, and the
    other is a problem that says which name was taken."""
    plugins_bundle = of(bundle(tmp_path, name="mailbox"))
    folder = tmp_path / "other-ui" / "mailbox"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({**MANIFEST, "name": "mailbox"}))
    (folder / "index.js").write_text("export default { folder: true }\n")
    harness = Harness(tmp_path, web=WebSettings(ui_plugin_dirs=[folder.parent]))
    harness.registry.register(UI, _as_ui_plugin("mailbox", plugins_bundle))

    async with client_for(harness) as client:
        listed = await client.get("/ui/plugins.json")
        entry = await client.get("/ui/plugins/mailbox/index.js")

    assert [plugin["name"] for plugin in listed.json()["plugins"]] == ["mailbox"]
    [problem] = listed.json()["problems"]
    assert (problem["origin"], "mailbox" in problem["error"]) == (str(folder), True)
    assert entry.text == "export default {}\n"  # (the plugin's, not the folder's)


def test_the_schema_s_isolation_is_closed() -> None:
    # (So the SDK's TypeScript type is a union of two, not a string.)
    isolation = UIManifest.model_json_schema()["properties"]["isolation"]

    assert isolation["enum"] == ["shadow", "none"]


async def test_the_api_lists_a_broken_bundle_as_a_problem(tmp_path: Path) -> None:
    folder = tmp_path / "ui-plugins"
    (folder / "half").mkdir(parents=True)
    (folder / "half" / "manifest.json").write_text("{}")
    harness = Harness(tmp_path, web=WebSettings(ui_plugin_dirs=[folder]))

    async with client_for(harness) as client:
        answer = await client.get("/ui/plugins.json")

    assert answer.status_code == 200
    assert answer.json()["plugins"] == []
    assert len(answer.json()["problems"]) == 1


async def test_a_plugin_that_brings_a_ui_is_said_to_have_one(tmp_path: Path) -> None:
    from udl.core.models import ListPluginsRequest

    harness = Harness(tmp_path, web=WebSettings(ui_plugin_dirs=[]))
    harness.registry.register(UI, _as_ui_plugin("gmail", of(bundle(tmp_path))))

    async with harness.service() as service:
        listed = await service.list_plugins(ListPluginsRequest())

    assert {plugin.name: plugin.ui for plugin in listed.plugins}["gmail"] is True


def test_the_sdk_version_is_the_package_s() -> None:
    package = Path(__file__).resolve().parents[3] / "web" / "packages" / "sdk" / "package.json"

    assert json.loads(package.read_text())["version"] == SDK_VERSION


def test_the_committed_schema_is_the_model_s() -> None:
    # (The SDK's TypeScript type is made from it: a change to the model is a change to it.)
    from udl.plugins.ui import base

    committed = json.loads(base.SCHEMA.read_text())

    assert committed == base.schema()


def test_the_schema_is_written_from_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from udl.plugins.ui import base

    written = tmp_path / "manifest.schema.json"
    monkeypatch.setattr(base, "SCHEMA", written)

    assert base.main(["--schema"]) == 0

    schema = json.loads(written.read_text())
    assert schema["title"] == "UIManifest"
    assert "contributes" in schema["properties"]
    assert UIManifest.model_json_schema()["title"] == "UIManifest"
