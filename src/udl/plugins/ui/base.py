"""
---------
- ui.py -
---------

UI plugins: a bundle of files (an ES module, its stylesheets, and a `manifest.json`) that the
shell loads into its pages. Any plugin of any kind may bring one (its `ui`), a UI-only plugin is
a `udl.ui` entry point, and a folder in `[web] ui_plugin_dirs` is one too. `GET /ui/plugins.json`
lists the valid ones, each with where its files are; a bundle that fails its checks is a problem,
never served.

The manifest is the contract between udl and the bundle, so it is checked here (its contribution
points are a closed set, its entry and styles are files of its own folder, and its `sdk` is a
`^MAJOR.MINOR` range the shell's SDK has) and published as a JSON Schema
(`python -m udl.plugins.ui --schema`), which `json-schema-to-typescript` turns into the TypeScript
type the SDK is written against.

Since 24.7 a manifest may also guide the page udl generates for its plugin (`generated`: which
tabs stay, what to hide, regroup, rename and reorder, and its own drawer entry), and contribute
three points that replace part of it (`operationViews`, `resultViews`, `fieldWidgets`). Guidance
that names no operation is a problem, not an error: the bundle is still served, and the page
ignores what matches nothing (`guidance_problems`).
"""

# System Imports
import json
import logging
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, Literal

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

# Local Imports
from udl.errors import PluginError

log = logging.getLogger(__name__)

SDK_VERSION: Final = "1.1.0"
"""The @udl/ui-sdk version the shell provides (`web/packages/sdk/package.json`'s)."""

SCHEMA: Final = (
    Path(__file__).resolve().parents[4]
    / "web"
    / "packages"
    / "sdk"
    / "src"
    / "manifest.schema.json"
)
"""Where the manifest's JSON Schema is written (its TypeScript type is made from it)."""

POINTS: Final = frozenset(
    {
        "nav",
        "routes",
        "settings",
        "accountSetup",
        "eventViews",
        "filterEditors",
        "actionEditors",
        "widgets",
        "themes",
        # (29.12: what the navbar opens at the right of the page.)
        "panels",
        # (24.7: what a plugin replaces in the page udl generates.)
        "operationViews",
        "resultViews",
        "fieldWidgets",
    }
)
"""Every contribution point the shell has: a manifest naming another is a problem."""

# (^MAJOR.MINOR, so Python checks it without an npm semver library.)
_SDK: Final = re.compile(r"^\^(\d+)\.(\d+)(?:\.\d+)?$")


class _Frozen(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_attribute_docstrings=True
    )


class NavItem(_Frozen):
    """A page in the shell's navigation."""

    title: str
    """What it says."""
    route: str
    """The route it goes to (one of the manifest's `routes`)."""
    icon: str | None = None
    """The icon's name (the shell maps it to lucide's)."""


class Widget(_Frozen):
    """Something the shell puts in one of its slots."""

    slot: str
    """Where: a slot the shell has (dashboard...)."""
    id: str
    """Which of the plugin's widgets it is (what its entry's `widgets` is keyed by)."""


class Panel(_Frozen):
    """Something the shell opens at the right of the page, from its own button (29.12)."""

    id: str = Field(min_length=1)
    """Which of the plugin's panels it is (what its entry's `panels` is keyed by)."""
    title: str = Field(min_length=1)
    """What its button in the navbar says."""
    icon: str | None = None
    """The icon's name (the shell maps it to lucide's)."""
    shortcut: str | None = None
    """The key that opens and closes it (`Ctrl+J`), when it has one."""


TABS: Final = ("operations", "settings", "accounts", "events")
"""The tabs a generated page has, in the order udl lists them."""

Tab = Literal["operations", "settings", "accounts", "events"]
"""One of them."""


class Contributions(_Frozen):
    """What a bundle adds to the shell, by point (each a closed set: see POINTS)."""

    nav: list[NavItem] = Field(default_factory=list[NavItem])
    """Pages in the navigation."""
    routes: list[str] = Field(default_factory=list[str])
    """The paths it has a page for (`inbox`, `message/$id`)."""
    settings: list[str] = Field(default_factory=list[str])
    """The plugins it has a settings panel for."""
    account_setup: list[str] = Field(default_factory=list[str], alias="accountSetup")
    """The integrations it has account setup for."""
    event_views: list[str] = Field(default_factory=list[str], alias="eventViews")
    """The event types it renders (a `*` at the end is a prefix)."""
    filter_editors: list[str] = Field(default_factory=list[str], alias="filterEditors")
    """The integrations it has a source-filter editor for."""
    action_editors: list[str] = Field(default_factory=list[str], alias="actionEditors")
    """The action kinds it has an editor for."""
    widgets: list[Widget] = Field(default_factory=list[Widget])
    """What it puts in the shell's slots."""
    themes: list[str] = Field(default_factory=list[str])
    """The daisyUI themes it adds."""
    panels: list[Panel] = Field(default_factory=list[Panel])
    """What it opens at the right of the page (29.12)."""
    operation_views: list[str] = Field(default_factory=list[str], alias="operationViews")
    """The operations whose panel it replaces (a mount of its own)."""
    result_views: list[str] = Field(default_factory=list[str], alias="resultViews")
    """The operations whose result it draws itself (a name, or a `*` prefix)."""
    field_widgets: list[str] = Field(default_factory=list[str], alias="fieldWidgets")
    """The field widgets it adds, by the name a field's `x-udl-ui.widget` asks for."""


class GeneratedNav(_Frozen):
    """What the drawer's entry for its plugin says, when the page is generated (24.7)."""

    title: str = Field(min_length=1)
    """What it says."""
    icon: str | None = None
    """The icon's name (the shell maps it to lucide's)."""


class Generated(_Frozen):
    """How a UI plugin guides the page udl generates for its plugin (24.7): everything here is
    optional, and what it does not say stays as udl answered it."""

    nav: GeneratedNav | Literal[False] | None = None
    """Its drawer entry: a title and an icon of its own, or False for no entry."""
    tabs: list[Tab] = Field(default_factory=list[Tab])
    """Which tabs stay, in this order (all of them, as udl lists them, when this is empty)."""
    hide: list[str] = Field(default_factory=list[str])
    """The operations not to show (a name, or a `*` prefix: `gmail_send_*`)."""
    groups: dict[str, list[str]] = Field(default_factory=dict[str, list[str]])
    """The operations to move under a group of this name (the group's own order is the dict's)."""
    order: list[str] = Field(default_factory=list[str])
    """The operations to put first, in this order."""
    labels: dict[str, str] = Field(default_factory=dict[str, str])
    """What to call an operation, instead of its title."""


class UIManifest(_Frozen):
    """A UI plugin's manifest.json: what it is, and what it adds to the shell."""

    name: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    """The plugin it belongs to (or a UI-only plugin's own name)."""
    version: str = Field(min_length=1)
    """Its version (its own: what the shell shows when something fails)."""
    sdk: str
    """The @udl/ui-sdk range it was built for: `^MAJOR.MINOR`, which must be the shell's."""
    entry: str = Field(min_length=1)
    """Its ES module, a file of the bundle."""
    styles: list[str] = Field(default_factory=list[str])
    """The stylesheets adopted into its shadow root (files of the bundle)."""
    isolation: Literal["shadow", "none"] = "shadow"
    """`shadow` (its own root, the default) or `none` (light DOM, the shell's CSS)."""
    contributes: Contributions = Field(default_factory=Contributions)
    """What it adds to the shell."""
    generated: Generated | None = None
    """How it guides the page udl generates for its plugin (24.7)."""


@dataclass(frozen=True)
class UIBundle:
    """A UI plugin's bundle: the folder, and its checked manifest."""

    dir: Path
    manifest: UIManifest

    @classmethod
    def of(cls, folder: Path) -> UIBundle:
        """The bundle in `folder`. Raises PluginError when its manifest.json is missing, is not
        a manifest, names a contribution point the shell does not have, an entry or stylesheet
        that is not a file of the bundle, or an SDK range the shell does not have."""
        manifest_file = folder / "manifest.json"
        try:
            text = manifest_file.read_text()
        except OSError as e:
            raise PluginError(f"{folder} is not a UI plugin's bundle: no manifest.json") from e
        try:
            manifest = UIManifest.model_validate_json(text)
        except ValidationError as e:
            raise PluginError(f"{manifest_file} is not a UI plugin's manifest: {_why(e)}") from e
        _check_sdk(manifest, manifest_file)
        for name, relative in [("entry", manifest.entry), *[("style", s) for s in manifest.styles]]:
            if _inside(folder, relative) is None:
                raise PluginError(
                    f"{manifest_file}: its {name} {relative!r} is not a file of the bundle"
                )
        return cls(dir=folder, manifest=manifest)

    @property
    def name(self) -> str:
        """The name the shell knows it by (its manifest's)."""
        return self.manifest.name


class UIPlugin:
    """A UI-only plugin (the `udl.ui` kind): a bundle, with no Python behaviour of its own."""

    name: ClassVar[str]
    ui: ClassVar[UIBundle]


class UIPluginInfo(_Frozen):
    """A UI plugin the shell can load: where its files are, and what it adds."""

    name: str
    """Its name (its manifest's: the plugin it belongs to, or its own)."""
    version: str
    """Its version."""
    base_url: str
    """Where its files are served from: /ui/plugins/<name>/."""
    entry_url: str
    """Its ES module."""
    styles: list[str] = Field(default_factory=list[str])
    """Its stylesheets, in the order they are adopted."""
    isolation: Literal["shadow", "none"]
    """`shadow` (its own root) or `none` (light DOM, the shell's CSS)."""
    contributes: Contributions
    """What it adds to the shell."""
    plugin: str | None = None
    """The plugin it belongs to (None for a UI-only plugin)."""
    generated: Generated | None = None
    """How it guides the page udl generates (24.7)."""
    problems: list[str] = Field(default_factory=list[str])
    """What its guidance names that no operation it reaches has (24.7): said, never fatal."""

    @classmethod
    def of(
        cls, found: UIBundle, *, plugin: str | None, problems: Iterable[str] = ()
    ) -> UIPluginInfo:
        """`found`'s entry in the list, as the shell asks for it, and what its guidance names
        that no operation it reaches has."""
        base = f"/ui/plugins/{found.name}/"
        return cls(
            name=found.name,
            version=found.manifest.version,
            base_url=base,
            entry_url=base + found.manifest.entry,
            styles=[base + style for style in found.manifest.styles],
            isolation=found.manifest.isolation,
            contributes=found.manifest.contributes,
            plugin=plugin,
            generated=found.manifest.generated,
            problems=list(problems),
        )


class UIBundleProblem(_Frozen):
    """A bundle the shell cannot load: what failed, and why (it is not served)."""

    origin: str
    """The bundle's folder, or the plugin that brought it."""
    error: str
    """What is wrong with it, in a line."""


class UIPluginList(_Frozen):
    """The UI plugins the shell can load, and the ones it cannot."""

    plugins: list[UIPluginInfo] = Field(default_factory=list[UIPluginInfo])
    """The valid ones, by name."""
    problems: list[UIBundleProblem] = Field(default_factory=list[UIBundleProblem])
    """The bundles that failed their checks (they are not served)."""


def guidance_problems(manifest: UIManifest, operations: Iterable[str]) -> list[str]:
    """What `manifest`'s guidance names that `operations` have not: a `hide` that matches none,
    or a label, a group, an order, an operation view or a result view for an operation it has
    not. `operations` is its own plugin's, or (a UI-only bundle's, whose guidance every page
    takes) every operation udl knows. They are problems, not errors: the bundle is still
    served, and the page ignores what matches nothing."""
    names = set(operations)
    guidance = manifest.generated
    contributes = manifest.contributes
    problems: list[str] = []
    if guidance is not None:
        for pattern in guidance.hide:
            if not any(matches(pattern, name) for name in names):
                problems.append(f'its guidance hides "{pattern}", and no operation matches')
        for what, named in [
            ("labels", guidance.labels),
            ("groups", [one for group in guidance.groups.values() for one in group]),
            ("orders", guidance.order),
        ]:
            for name in named:
                if name not in names:
                    problems.append(_names_nothing(f"its guidance {what}", name))
    for point, keys in [
        ("operationViews", contributes.operation_views),
        ("resultViews", contributes.result_views),
    ]:
        for key in keys:
            if not any(matches(key, name) for name in names):
                problems.append(_names_nothing(f"its {point} names", key))
    return problems


def matches(pattern: str, name: str) -> bool:
    """Whether `pattern` names `name`: itself, or its prefix when it ends with `*`."""
    return name.startswith(pattern[:-1]) if pattern.endswith("*") else pattern == name


def _names_nothing(what: str, name: str) -> str:
    """The problem of a guidance (or a point) that names no operation."""
    return f'{what} "{name}", and it has no operation of that name'


def _check_sdk(manifest: UIManifest, where: Path) -> None:
    """Raise PluginError unless `manifest`'s `sdk` is a range the shell's SDK satisfies: the
    same major, and a minor no higher."""
    match = _SDK.match(manifest.sdk)
    if match is None:
        raise PluginError(
            f"{where}: sdk is {manifest.sdk!r}, not a ^MAJOR.MINOR range",
            hint=f"The shell has @udl/ui-sdk {SDK_VERSION}.",
        )
    major, minor = (int(part) for part in match.groups())
    shell_major, shell_minor = (int(part) for part in SDK_VERSION.split(".")[:2])
    # (The same major, and a minor no higher: ^0.9 is not ^1.0's, and ^1.1 wants more.)
    if major != shell_major or minor > shell_minor:
        raise PluginError(
            f"{where}: it was built for @udl/ui-sdk {manifest.sdk}, and this shell has "
            f"{SDK_VERSION}",
            hint="Rebuild it against the shell's SDK.",
        )


def _inside(folder: Path, relative: str) -> Path | None:
    """`relative` in `folder`, when it is a file there and not elsewhere (no `..`, no symlink
    out): a bundle's files are its own."""
    try:
        path = (folder / relative).resolve()
    except OSError, ValueError:
        return None
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        return None
    return path


def _why(error: ValidationError) -> str:
    """A validation error's problems, in a line."""
    return "; ".join(
        f"{'.'.join(map(str, problem['loc'])) or 'manifest'}: {problem['msg']}"
        for problem in error.errors()
    )


def schema() -> dict[str, JsonValue]:
    """The manifest's JSON Schema (its contribution points and all), for the SDK's types."""
    return UIManifest.model_json_schema()


def write_schema() -> Path:
    """Write the manifest's JSON Schema where the SDK's types are made from it."""
    SCHEMA.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA.write_text(json.dumps(schema(), indent="\t") + "\n")
    return SCHEMA


def main(argv: list[str] | None = None) -> int:
    """`python -m udl.plugins.ui --schema`: write the schema (the one thing there is to do)."""
    args = sys.argv[1:] if argv is None else argv
    if args != ["--schema"]:
        sys.stderr.write("usage: python -m udl.plugins.ui --schema\n")
        return 2
    sys.stdout.write(f"Wrote {write_schema()}\n")
    return 0
