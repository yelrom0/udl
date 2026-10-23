"""
-------------
- engine.py -
-------------

Helpers for tests of the resolution engine and the service: settings with in-memory databases,
fake downloaders under any name (scripted through the settings' [tools.<name>] tables, like any
downloader's options) and a registry of them
"""

# System Imports
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Package Imports
from pydantic import JsonValue, SecretStr

# Local Imports
from support.downloaders import FakeDownloader, FakeMedia, FakeOptions
from udl.config import DBConnectionConfig, DBSettings, ReadPolicy, Settings, WritePolicy
from udl.db.manager import DBManager
from udl.plugins.databases.memory import MemoryDatabase
from udl.plugins.downloaders.base import Cookie
from udl.plugins.registry import DATABASES, DOWNLOADERS, PluginRegistry

TOOLS = ("http", "yt-dlp", "gallery-dl", "scraper", "browser")


def fake_tool(tool_name: str) -> type[FakeDownloader]:
    """A FakeDownloader called `tool_name`."""

    class Tool(FakeDownloader):
        name = tool_name

    Tool.__qualname__ = Tool.__name__ = f"Fake[{tool_name}]"
    return Tool


def registry(*tools: str) -> PluginRegistry:
    """A registry of the memory database and fake downloaders called `tools` (by default, the
    names of udl's own downloaders)."""
    plugins = PluginRegistry()
    plugins.register(DATABASES, MemoryDatabase)
    for tool in tools or TOOLS:
        plugins.register(DOWNLOADERS, fake_tool(tool))
    return plugins


def _media(media: Mapping[str, FakeMedia | str] | None) -> dict[str, FakeMedia]:
    return {
        url: FakeMedia(content=item.encode()) if isinstance(item, str) else item
        for url, item in (media or {}).items()
    }


def script(
    media: Mapping[str, FakeMedia | str] | None = None,
    *,
    errors: Mapping[str, str] | None = None,
    matches: Mapping[str, float] | None = None,
    confidence: float = 0.8,
    chained: Mapping[str, FakeMedia | str] | None = None,
    suggested: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, JsonValue]:
    """A fake tool's [tools.<name>] table: the media each URL gives (FakeMedia, or just its
    content), the URLs that fail (with an ErrorCode), the URLs it matches offline, the media
    each URL gives when the tool follows a tag chain and the chains it suggests for a page
    ({chain: its media's URL})."""
    options = FakeOptions.model_validate(
        {
            "media": _media(media),
            "chained": _media(chained),
            "suggested": {url: dict(chains) for url, chains in (suggested or {}).items()},
            "errors": dict(errors or {}),
            "matches": dict(matches or {}),
            "confidence": confidence,
        }
    )
    return options.model_dump(mode="json")


def connection(name: str = "local", **fields: Any) -> DBConnectionConfig:
    return DBConnectionConfig(name=name, plugin="memory", url="memory://", **fields)


def settings(
    tmp_path: Path,
    *,
    tools: Mapping[str, dict[str, JsonValue]] | None = None,
    connections: list[DBConnectionConfig] | None = None,
    read_policy: ReadPolicy = ReadPolicy.FIRST,
    write_policy: WritePolicy | list[str] = WritePolicy.PRIMARY,
    **fields: Any,
) -> Settings:
    """Settings that keep everything in memory (or under tmp_path), with the fake tools'
    scripts."""
    fields.setdefault("probe_order", list(TOOLS))
    fields.setdefault("download_dir", tmp_path / "downloads")
    return Settings(
        tools=dict(tools or {}),
        db=DBSettings(
            connections=connections or [connection(primary=True)],
            read_policy=read_policy,
            write_policy=write_policy,
        ),
        **fields,
    )


def databases(config: Settings, plugins: PluginRegistry) -> DBManager:
    return DBManager(config.db, plugins)


def cookie(name: str = "session", value: str = "signed-in", domain: str = "example.com") -> Cookie:
    return Cookie(name=name, value=SecretStr(value), domain=domain)


class StaticCookies:
    """A cookie source with the same cookies for every URL. It counts the times it is asked."""

    def __init__(self, cookies: list[Cookie] | None = None) -> None:
        self.cookies = cookies or []
        self.asked = 0

    async def for_url(self, url: str) -> list[Cookie]:
        self.asked += 1
        return [cookie for cookie in self.cookies if cookie.matches(url)]
