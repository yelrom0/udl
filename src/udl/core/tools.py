"""
------------
- tools.py -
------------

Toolbox: the registered downloaders, configured. A downloader's options are layered: the
[browser] table (for the browser downloader, which runs as it says), its [tools.<name>] table,
then the options of the strategy it runs for.
"""

# System Imports
from collections.abc import Mapping
from typing import Any

# Package Imports
from pydantic import JsonValue

# Local Imports
from udl.config import Settings
from udl.plugins.downloaders.base import Downloader
from udl.plugins.registry import DOWNLOADERS, PluginRegistry

_BROWSER = "browser"


class Toolbox:
    """Gives the downloaders by name, configured."""

    def __init__(self, settings: Settings, registry: PluginRegistry) -> None:
        self._settings = settings
        self._registry = registry

    @property
    def names(self) -> list[str]:
        """The registered downloaders' names, sorted."""
        return [info.name for info in self._registry.plugins(DOWNLOADERS)]

    def has(self, name: str) -> bool:
        return name in self.names

    def get(self, name: str, options: Mapping[str, JsonValue] | None = None) -> Downloader[Any]:
        """The downloader called `name`, with `options` (e.g. a strategy's) over the settings'.
        Raises PluginError when there is none, ConfigError when the options are invalid."""
        cls = self._registry.get(DOWNLOADERS, name)
        layers: list[Mapping[str, JsonValue]] = []
        if name == _BROWSER:
            browser = self._settings.browser
            fields = cls.options_model.model_fields
            settings: dict[str, JsonValue] = {
                "headless": browser.headless,
                "profile_dir": str(browser.profile_dir),
            }
            layers.append({key: value for key, value in settings.items() if key in fields})
        layers += [self._settings.tools.get(name, {}), options or {}]
        return cls.configure(*layers)

    def downloader_for(self, name: str) -> Downloader[Any]:
        """The downloader a handoff names (a DownloadContext's downloader_for)."""
        return self.get(name)
