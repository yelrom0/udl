"""
---------------------------------
- test_ui.py (the orch plugin's) -
---------------------------------

29.2: the orchestrator's UI is its own bundle, built beside its plugin by `make web`. A checkout
that has not built it has no bundle (the generated page alone), and a bundle that is there but
broken is refused loudly rather than silently dropped.
"""

# System Imports
import json
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from udl.errors import PluginError
from udl.plugins.apps.orch.plugin import Orch, ui_bundle
from udl.plugins.ui.base import UIBundle


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


def test_a_checkout_that_has_not_built_the_ui_has_no_bundle(tmp_path: Path) -> None:
    assert ui_bundle(tmp_path / "ui") is None


def test_a_built_bundle_is_the_plugin_s_ui(tmp_path: Path) -> None:
    folder = written(tmp_path / "ui")

    bundle = ui_bundle(folder)

    assert isinstance(bundle, UIBundle)
    assert bundle.name == "orch"
    assert bundle.dir == folder
    assert list(bundle.manifest.contributes.routes or []) == ["inbox"]


def test_a_bundle_that_is_there_but_broken_is_refused(tmp_path: Path) -> None:
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

    with pytest.raises(PluginError, match=r"gone\.js"):
        ui_bundle(folder)


def test_the_orchestrator_brings_its_ui_when_it_is_built() -> None:
    # (The plugin's own bundle is `make web`'s: what it is, when it exists, is `Orch.ui`.)
    assert Orch.ui is None or isinstance(Orch.ui, UIBundle)
    if Orch.ui is not None:
        assert Orch.ui.name == "orch"
