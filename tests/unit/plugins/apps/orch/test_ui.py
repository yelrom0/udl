"""
---------------------------------
- test_ui.py (the orch plugin's) -
---------------------------------

29.2: the orchestrator's UI is its own bundle, built beside its plugin by `make web`. The plugin
names the folder (`Orch.ui_folder`) and never loads it itself: a checkout that has not built it
has no bundle, and a folder that is there but broken is a problem `GET /ui/plugins.json` says —
never an import that takes the CLI, the API and the MCP down with it.
"""

# System Imports
import json
from pathlib import Path

# Local Imports
from support.engine import settings
from udl.config import WebSettings
from udl.plugins.apps.orch.plugin import UI_FOLDER, Orch
from udl.plugins.registry import APPS, PluginRegistry
from udl.plugins.ui.discovery import ui_plugins


def written(folder: Path, *, manifest: dict[str, object] | None = None) -> Path:
    """A built bundle in `folder`: a manifest, an entry and a stylesheet."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.js").write_text("export default {};\n")
    (folder / "index.css").write_text("/* the plugin's own */\n")
    (folder / "manifest.json").write_text(
        json.dumps(
            manifest
            or {
                "name": "orch",
                "version": "0.1.0",
                "sdk": "^1.1",
                "entry": "index.js",
                "styles": ["index.css"],
                "isolation": "shadow",
                "contributes": {"routes": ["inbox"], "nav": [{"title": "Inbox", "route": "inbox"}]},
            }
        )
    )
    return folder


def registered(folder: Path) -> PluginRegistry:
    """A registry with the orchestrator in it, its UI in `folder`."""
    plugins = PluginRegistry()
    plugins.register(APPS, type("Orch", (Orch,), {"ui_folder": folder}))
    return plugins


def test_the_plugin_names_the_folder_the_web_build_writes_into() -> None:
    assert Path("src/udl/plugins/apps/orch/ui").resolve() == UI_FOLDER
    assert Orch.ui_folder == UI_FOLDER
    # (Never loaded at import: a half-written folder is a problem on the list, not a dead CLI.)
    assert Orch.ui is None


def test_a_checkout_that_has_not_built_the_ui_lists_no_bundle(tmp_path: Path) -> None:
    found = ui_plugins(settings(tmp_path, web=WebSettings(ui_plugin_dirs=[])), registered(tmp_path))

    assert (found.plugins, found.problems) == ([], [])


def test_a_built_bundle_is_the_plugin_s_ui(tmp_path: Path) -> None:
    folder = written(tmp_path / "ui")

    found = ui_plugins(settings(tmp_path, web=WebSettings(ui_plugin_dirs=[])), registered(folder))

    assert [(one.name, one.plugin) for one in found.plugins] == [("orch", "orch")]
    assert found.plugins[0].base_url == "/ui/plugins/orch/"
    assert found.problems == []


def test_a_bundle_that_is_there_but_broken_is_a_problem_not_an_error(tmp_path: Path) -> None:
    folder = written(
        tmp_path / "ui",
        manifest={
            "name": "orch",
            "version": "0.1.0",
            "sdk": "^1.1",
            "entry": "gone.js",
            "isolation": "shadow",
        },
    )

    found = ui_plugins(settings(tmp_path, web=WebSettings(ui_plugin_dirs=[])), registered(folder))

    assert found.plugins == []
    assert len(found.problems) == 1
    assert "gone.js" in found.problems[0].error
