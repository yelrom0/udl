"""
---------------
- registry.py -
---------------

PluginRegistry: finds plugins of every kind (databases, downloaders, site behaviour, secret
stores, actions).

Plugins come from entry points (udl's built-ins are registered the same way third-party ones are)
and from the `*.py` files in the configured plugin dirs. Later sources override earlier ones:
built-in entry points, then other installed packages' entry points, then each plugin dir in order.
"""

# System Imports
import hashlib
import importlib.util
import inspect
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Self, cast

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.errors import PluginError
from udl.plugins.actions.base import ActionPlugin
from udl.plugins.databases.base import DatabasePlugin
from udl.plugins.downloaders.base import Downloader
from udl.plugins.secrets.base import SecretStore
from udl.plugins.sites.base import SitePlugin

log = logging.getLogger(__name__)

_DISTRIBUTION: Final[str] = "udl"


@dataclass(frozen=True)
class PluginKind[P]:
    """A type of plugin: the entry point group it is published under and the class it extends."""

    group: str
    base: type[P]


class PluginSource(StrEnum):
    """Where a plugin came from."""

    BUILTIN = "builtin"  # an entry point of udl itself
    INSTALLED = "installed"  # an entry point of another installed package
    LOCAL = "local"  # a file in a plugin dir, or registered in code


class PluginInfo(BaseModel):
    """A registered plugin."""

    model_config = ConfigDict(frozen=True)

    kind: str  # the kind's entry point group, e.g. "udl.databases"
    name: str
    source: PluginSource
    origin: str  # "module:attribute" for entry points and code, the file for plugin dirs
    overrides: str | None = None  # the origin of the plugin of the same name it replaced


class PluginProblem(BaseModel):
    """A plugin that could not be loaded. Problems are reported instead of stopping udl."""

    model_config = ConfigDict(frozen=True)

    kind: str | None  # None when a plugin file failed before any plugin was found in it
    origin: str
    error: str


DATABASES: Final = PluginKind("udl.databases", DatabasePlugin)
DOWNLOADERS: Final = PluginKind[Downloader[Any]]("udl.downloaders", Downloader)
SITES: Final = PluginKind("udl.sites", SitePlugin)
SECRETS: Final = PluginKind("udl.secrets", SecretStore)
ACTIONS: Final = PluginKind[ActionPlugin[Any]]("udl.actions", ActionPlugin)

# Every kind of plugin udl uses.
PLUGIN_KINDS: Final[tuple[PluginKind[Any], ...]] = (DATABASES, DOWNLOADERS, SITES, SECRETS, ACTIONS)


@dataclass(frozen=True)
class _Entry:
    cls: type[Any]
    info: PluginInfo


class PluginRegistry:
    """The plugins of each kind, by name."""

    def __init__(self, kinds: Iterable[PluginKind[Any]] = PLUGIN_KINDS) -> None:
        self._kinds = list(kinds)
        self._plugins: dict[str, dict[str, _Entry]] = {kind.group: {} for kind in self._kinds}
        self._problems: list[PluginProblem] = []

    @classmethod
    def discover(
        cls, plugin_dirs: Iterable[Path] = (), *, kinds: Iterable[PluginKind[Any]] = PLUGIN_KINDS
    ) -> Self:
        """A registry with every installed plugin, then the plugins in `plugin_dirs`."""
        registry = cls(kinds)
        registry.load_entry_points()
        for directory in plugin_dirs:
            registry.load_dir(directory)
        return registry

    @property
    def problems(self) -> list[PluginProblem]:
        """The plugins that failed to load."""
        return list(self._problems)

    def plugins(self, kind: PluginKind[Any] | None = None) -> list[PluginInfo]:
        """The registered plugins (of one kind, or of every kind), sorted by kind and name."""
        groups = [kind.group] if kind else sorted(self._plugins)
        return [
            entry.info
            for group in groups
            for _, entry in sorted(self._plugins.get(group, {}).items())
        ]

    def entries(self) -> list[tuple[PluginInfo, type[Any]]]:
        """Every registered plugin and its class, sorted by kind and name."""
        return [
            (entry.info, entry.cls)
            for group in sorted(self._plugins)
            for _, entry in sorted(self._plugins[group].items())
        ]

    def get[P](self, kind: PluginKind[P], name: str) -> type[P]:
        """The plugin class named `name`. Raises PluginError when there is none."""
        if kind.group not in self._plugins:
            raise PluginError(f"This registry has no {kind.group} plugins")
        entry = self._plugins[kind.group].get(name)
        if entry is None:
            known = ", ".join(sorted(self._plugins[kind.group])) or "none"
            raise PluginError(
                f"No {kind.group} plugin named {name!r}", hint=f"Available plugins: {known}."
            )
        return cast(type[P], entry.cls)

    def register[P](
        self,
        kind: PluginKind[P],
        cls: type[P],
        *,
        source: PluginSource = PluginSource.LOCAL,
        origin: str | None = None,
    ) -> None:
        """Register a plugin class under its `name`, replacing (with a warning) one of that name."""
        if not (inspect.isclass(cls) and issubclass(cls, kind.base)):
            raise PluginError(f"{cls!r} is not a {kind.group} plugin")
        name = getattr(cls, "name", None)
        if not isinstance(name, str) or not name:
            raise PluginError(f"{cls.__qualname__} has no name")
        plugins = self._plugins[kind.group]
        old = plugins.get(name)
        info = PluginInfo(
            kind=kind.group,
            name=name,
            source=source,
            origin=origin or f"{cls.__module__}:{cls.__qualname__}",
            overrides=old.info.origin if old else None,
        )
        if old:
            log.warning(
                "The %s plugin %r from %s overrides the one from %s",
                kind.group,
                name,
                info.origin,
                old.info.origin,
            )
        plugins[name] = _Entry(cls, info)

    def load_entry_points(self) -> None:
        """Register the plugins every installed package publishes, udl's own first."""
        for kind in self._kinds:
            found = sorted(entry_points(group=kind.group), key=_builtin_first)
            for entry_point in found:
                self._load_entry_point(kind, entry_point)

    def load_dir(self, directory: Path) -> None:
        """Register the plugin classes defined in each `*.py` file of `directory`."""
        if not directory.is_dir():
            log.debug("Plugin dir %s does not exist", directory)
            return
        for path in sorted(directory.glob("*.py")):
            self._load_file(path)

    def _load_entry_point(self, kind: PluginKind[Any], entry_point: EntryPoint) -> None:
        source = PluginSource.BUILTIN if _is_builtin(entry_point) else PluginSource.INSTALLED
        try:
            cls = entry_point.load()
            if getattr(cls, "name", entry_point.name) != entry_point.name:
                raise PluginError(
                    f"The entry point is {entry_point.name!r} but the plugin is named {cls.name!r}"
                )
            self.register(kind, cls, source=source, origin=entry_point.value)
        except Exception as e:  # noqa: BLE001 - a broken plugin must never stop udl
            self._report(kind, entry_point.value, e)

    def _load_file(self, path: Path) -> None:
        try:
            module = _import_file(path)
        except Exception as e:  # noqa: BLE001 - a broken plugin must never stop udl
            self._report(None, str(path), e)
            return
        for cls in vars(module).values():
            if not isinstance(cls, type) or cls.__module__ != module.__name__:
                continue  # not a class, or one the file only imports
            for kind in self._kinds:
                if issubclass(cls, kind.base) and not inspect.isabstract(cls):
                    try:
                        self.register(kind, cls, source=PluginSource.LOCAL, origin=str(path))
                    except PluginError as e:
                        self._report(kind, str(path), e)

    def _report(self, kind: PluginKind[Any] | None, origin: str, error: Exception) -> None:
        self.report(kind.group if kind else None, origin, error)

    def report(self, kind: str | None, origin: str, error: Exception) -> None:
        """Report a plugin (of the kind whose entry point group is `kind`, at `origin`) that
        could not be loaded, or used: it is one of the problems, rather than stopping udl."""
        message = (
            str(error) if isinstance(error, PluginError) else f"{type(error).__name__}: {error}"
        )
        self._problems.append(PluginProblem(kind=kind, origin=origin, error=message))
        log.warning("Could not load the plugin %s: %s", origin, message)


def _is_builtin(entry_point: EntryPoint) -> bool:
    return entry_point.dist is not None and entry_point.dist.name == _DISTRIBUTION


def _builtin_first(entry_point: EntryPoint) -> tuple[bool, str]:
    return (not _is_builtin(entry_point), entry_point.name)


def _import_file(path: Path) -> ModuleType:
    """Import a plugin file as a module of its own (its classes need it in sys.modules)."""
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    name = f"_udl_local_plugin_{digest}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - a *.py file always has a loader
        raise ImportError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module
