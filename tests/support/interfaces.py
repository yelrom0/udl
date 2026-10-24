"""
-----------------
- interfaces.py -
-----------------

A udl on fakes for the interfaces' tests (CLI, API, MCP): fake downloaders scripted through the
settings, and in-memory databases that keep what they hold from one service to the next (each
CLI run makes its own service)
"""

# System Imports
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

# Package Imports
from pydantic import JsonValue

# Local Imports
from support.downloaders import FakeMedia
from support.engine import StaticCookies, databases, registry, script, settings
from udl.clock import Clock
from udl.config import Settings
from udl.core.executor import RetryPolicy
from udl.core.jobs import InstanceLock
from udl.core.service import UDLService
from udl.db.manager import DBManager
from udl.plugins.secrets.memory import MemorySecretStore

A = "https://example.com/a"
B = "https://example.com/b"
SLOW = FakeMedia(content=b"x" * 2000, delay=0.02)


def media(files: Mapping[str, FakeMedia | str] | None = None) -> dict[str, dict[str, JsonValue]]:
    """[tools] tables in which http downloads `files` (by default A and B)."""
    files = files or {
        A: FakeMedia(content=b"a", filename="a.mp4", title="A"),
        B: FakeMedia(content=b"bb", filename="b.mp4"),
    }
    return {"http": script(files)}


class Harness:
    """Makes services on the fakes. The settings an interface loaded (e.g. the CLI's, with its
    --config and -j) are used, with the harness's databases, tools and download dir."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        tools: Mapping[str, dict[str, JsonValue]] | None = None,
        clock: Clock | None = None,
        **fields: Any,
    ) -> None:
        self.tmp_path = tmp_path
        self.settings = settings(tmp_path, tools=media() if tools is None else tools, **fields)
        self.registry = registry()
        self.clock = clock  # (the services' and the databases', else the system's)
        self.db: DBManager = databases(self.settings, self.registry, clock=clock)
        # (One store for every service, as the user's keyring is.)
        self.secrets = MemorySecretStore(self.settings.secrets)
        self.cookies = StaticCookies()
        self.loaded: list[Settings] = []  # the settings each service was made with

    def service(self, loaded: Settings | None = None) -> UDLService:
        """A service (not started yet) on the fakes, with `loaded`'s other settings."""
        config = self.settings
        if loaded is not None:
            config = loaded.model_copy(
                update={
                    field: getattr(self.settings, field)
                    for field in ("db", "tools", "integrations", "probe_order", "download_dir")
                }
            )
        self.loaded.append(config)
        return UDLService(
            config,
            registry=self.registry,
            databases=self.db,
            secrets=self.secrets,
            clock=self.clock,
            cookies=self.cookies,
            retry=RetryPolicy(retries=0),
            lock=InstanceLock(self.tmp_path / "udl.lock"),
            progress_interval=0,
        )

    def seed(self, work: Callable[[DBManager], Awaitable[object]]) -> None:
        """Put something in the databases, from a test that is not async (as the CLI's are)."""

        async def seed() -> None:
            async with self.db:
                await work(self.db)

        asyncio.run(seed())
