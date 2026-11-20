"""
-------------------
- discovery.py -
-------------------

Where UI plugins come from: each plugin's `ui` (of any kind, the `udl.ui` kind's included), then
each folder of `[web] ui_plugin_dirs` that holds a manifest.json. A bundle that fails its checks is
one of the problems, and is not served.
"""

# System Imports
import logging
from pathlib import Path

# Local Imports
from udl.config import Settings
from udl.core.operations import OperationRegistry
from udl.errors import PluginError
from udl.plugins.registry import UI, PluginRegistry
from udl.plugins.ui.base import (
    UIBundle,
    UIBundleProblem,
    UIPluginInfo,
    UIPluginList,
    guidance_problems,
)

log = logging.getLogger(__name__)


def ui_plugins(
    settings: Settings, plugins: PluginRegistry, ops: OperationRegistry | None = None
) -> UIPluginList:
    """Every UI plugin the shell can load: each plugin's `ui` (of any kind, the `udl.ui` kind's
    included), then each folder of `[web] ui_plugin_dirs` with a manifest.json. A bundle that
    fails is one of the problems (and is not served), and what a bundle's guidance names that
    no operation it reaches has is said on it (`ops`: the operations, when they are known)."""
    found, problems = _bundles(settings, plugins)
    return UIPluginList(
        plugins=[
            UIPluginInfo.of(bundle, plugin=owner, problems=_guidance_of(bundle, owner, ops))
            for _, (bundle, owner) in sorted(found.items())
        ],
        problems=problems,
    )


def _guidance_of(bundle: UIBundle, owner: str | None, ops: OperationRegistry | None) -> list[str]:
    """What `bundle`'s guidance names that no operation it reaches has: its plugin's own, or
    (a UI-only bundle's, whose guidance every page takes) every operation udl knows."""
    if ops is None:
        return []
    names = ops.names() if owner is None else [op.name for op in ops.in_namespace(owner)]
    return guidance_problems(bundle.manifest, names)


def ui_bundles(settings: Settings, plugins: PluginRegistry) -> dict[str, UIBundle]:
    """The bundle behind each name /ui/plugins/<name>/ serves (only a bundle's own files): what
    `ui_plugins` lists, by name, so the shell can load what it was told about. A bundle that
    fails, or that takes a name another one has, is a problem there and is not served."""
    found, _ = _bundles(settings, plugins)
    return {name: bundle for name, (bundle, _) in found.items()}


def _bundles(
    settings: Settings, plugins: PluginRegistry
) -> tuple[dict[str, tuple[UIBundle, str | None]], list[UIBundleProblem]]:
    """Each bundle the shell can load, by name, with the plugin it belongs to (None for a
    UI-only one), and what could not be loaded. A plugin's come first, then a folder's; a name
    two bundles take is the first one's, and the other is a problem (so the list and the files
    served are the same bundle)."""
    found: dict[str, tuple[UIBundle, str | None]] = {}
    problems: list[UIBundleProblem] = []
    for info, cls in plugins.entries():
        bundle = _bundle_of(info.name, info.origin, cls)
        if isinstance(bundle, UIBundleProblem):
            problems.append(bundle)
        elif bundle is not None:
            owner = None if info.kind == UI.group else info.name
            _take(found, problems, bundle, owner, origin=info.origin)
    for folder in _folders(settings):
        try:
            bundle = UIBundle.of(folder)
        except PluginError as e:
            problems.append(UIBundleProblem(origin=str(folder), error=e.message))
            continue
        _take(found, problems, bundle, None, origin=str(folder))
    return found, problems


def _take(
    found: dict[str, tuple[UIBundle, str | None]],
    problems: list[UIBundleProblem],
    bundle: UIBundle,
    owner: str | None,
    *,
    origin: str,
) -> None:
    """Keep `bundle` under its name, or record it as a problem when another one has it."""
    taken = found.get(bundle.name)
    if taken is None:
        found[bundle.name] = (bundle, owner)
        return
    problems.append(
        UIBundleProblem(
            origin=origin,
            error=f"its name {bundle.name!r} is {taken[0].dir}'s too, and that one is the "
            "one the shell loads",
        )
    )


def _folders(settings: Settings) -> list[Path]:
    """The folders of `[web] ui_plugin_dirs` that hold a manifest.json."""
    found: list[Path] = []
    for directory in settings.web.ui_plugin_dirs:
        if not directory.is_dir():
            log.debug("UI plugin dir %s does not exist", directory)
            continue
        found.extend(
            sorted(folder for folder in directory.iterdir() if (folder / "manifest.json").is_file())
        )
    return found


def _bundle_of(name: str, origin: str, cls: type[object]) -> UIBundle | UIBundleProblem | None:
    """The bundle a plugin brings (its `ui`), a problem when that is not a bundle, or None."""
    bundle = getattr(cls, "ui", None)
    if bundle is None:
        return None
    if not isinstance(bundle, UIBundle):
        return UIBundleProblem(origin=origin, error=f"{name}'s ui is {bundle!r}, not a UIBundle")
    return bundle
