"""
--------------
- manager.py -
--------------

Hosts (UDLService.hosts): the machines agents run on, as the operations see them.

`local` is always there (made the first time udl runs, so a host is never missing), and cannot
be deleted or turned into anything else. Another host is added by name; its `ssh` is a
`~/.ssh/config` host name, never a command line the user typed. Checking one runs `uname -a`
there and looks for the tools the runners need (git, gh, claude, opencode).

What a host can do is a HostShell (udl.hosts.shell_for): nothing here knows which transport a
host uses, and neither does anything above it.
"""

# System Imports
import logging
from typing import TYPE_CHECKING

# Package Imports
from pydantic import ValidationError

# Local Imports
from udl.config import data_dir
from udl.db.models import JsonObject
from udl.db.records import RecordQuery
from udl.errors import DoesNotExist, NotAllowed
from udl.hosts.models import HostCheck, HostInfo, HostList, SetHostRequest, ToolStatus
from udl.hosts.shell import LOCAL, Host, HostShell, shell_for

if TYPE_CHECKING:
    from udl.core.service import UDLService

log = logging.getLogger(__name__)

# The tools the runners need on a host (25.8's and 25.9's), each looked for with `--version`.
TOOLS: tuple[str, ...] = ("git", "gh", "claude", "opencode")


class Hosts:
    """The hosts of a service, and what checking one found."""

    def __init__(self, service: UDLService) -> None:
        self._service = service

    # -- reading -------------------------------------------------------------------------------

    async def all(self) -> list[Host]:
        """Every host, local first, then by name."""
        found = await self._service.db.list_records(Host, RecordQuery())
        return sorted(found, key=lambda host: (host.name != LOCAL, host.name))

    async def get(self, name: str) -> Host:
        """The host called `name`, making `local` if this is the first time it is asked for.
        Raises DoesNotExist (naming the hosts there are) for any other there is not."""
        found = await self._service.db.list_records(
            Host, RecordQuery(where={"name": name}, limit=1)
        )
        if found:
            return found[0]
        if name == LOCAL:
            return await self._make_local()
        others = [host.name for host in await self.all()]
        raise DoesNotExist(f"No host called {name!r}", hint=f"The hosts: {', '.join(others)}.")

    async def _make_local(self) -> Host:
        """The `local` host, made and kept (it is this machine, so it is always there)."""
        host = Host.local(str(data_dir() / "work"))
        saved = await self._service.db.upsert_record(host)
        log.info("Made the local host")
        return saved

    async def shell(self, name: str) -> HostShell:
        """The shell that acts on the host called `name` (its own, or an SSH one)."""
        return shell_for(await self.get(name))

    def shown(self, host: Host) -> HostInfo:
        return HostInfo.of(host)

    async def shown_list(self) -> HostList:
        """Every host, as the operations show them (local first)."""
        await self.get(LOCAL)  # (makes it, the first time)
        return HostList(hosts=[self.shown(host) for host in await self.all()])

    # -- changing ------------------------------------------------------------------------------

    async def set(self, request: SetHostRequest) -> HostInfo:
        """Add the host `request.host` names, or change the one that is there (`update` names the
        fields to change)."""
        found = await self._service.db.list_records(
            Host, RecordQuery(where={"name": request.host.name}, limit=1)
        )
        host = self._changed(found[0], dict(request.update)) if found else request.host
        saved = await self._service.db.upsert_record(host)
        log.info("Saved the host %s", saved.name)
        return self.shown(saved)

    def _changed(self, host: Host, update: JsonObject) -> Host:
        """`host` with the fields `update` names. Raises NotAllowed for a field it has none of,
        or a change that does not fit its transport."""
        unknown = sorted(set(update) - set(Host.model_fields))
        if unknown:
            raise NotAllowed(
                f"There is no such field: {', '.join(unknown)}",
                hint=f"The host has: {', '.join(sorted(Host.model_fields))}.",
            )
        # (Validated first, and only then kept: an invalid record would be re-validated on every
        # read, so it must never reach the database.)
        try:
            changed = type(host).model_validate({**host.model_dump(), **update})
        except ValidationError as e:
            raise NotAllowed(f"That change does not fit the host {host.name!r}: {e}") from e
        if changed.name != host.name:
            raise NotAllowed(
                f"The host {host.name!r} cannot be renamed to {changed.name!r}",
                hint="Add a new host, and delete this one.",
            )
        if changed.transport is not host.transport:
            raise NotAllowed(
                f"{host.name!r} is reached over {host.transport.value}: it cannot be changed to "
                f"{changed.transport.value}",
                hint="Add another host for the other transport, and delete this one.",
            )
        return changed

    async def delete(self, name: str) -> bool:
        """Delete the host called `name`. Raises NotAllowed for `local` (always there) and
        DoesNotExist for one there is not."""
        if name == LOCAL:
            raise NotAllowed(
                f"{LOCAL!r} is this machine and is always there: it cannot be deleted",
                hint="Set another host's `enabled` to false to leave it alone.",
            )
        host = await self.get(name)
        return await self._service.db.delete_record(Host, host.id)

    # -- checking ------------------------------------------------------------------------------

    async def check(self, name: str) -> HostCheck:
        """Run `uname -a` on the host, then look for the tools the runners need: whether udl can
        act there at all, and what it has."""
        shell = await self.shell(name)
        try:
            uname = await shell.run(["uname", "-a"])
        except Exception as e:  # noqa: BLE001 - (any failure is reported, not raised)
            return HostCheck(host=name, ok=False, problem=str(e))
        return HostCheck(
            host=name, ok=True, uname=uname.stdout.strip(), tools=await self.tools(shell)
        )

    @staticmethod
    async def tools(shell: HostShell) -> list[ToolStatus]:
        """Each tool a runner needs, and whether it is on the host (and its version).

        A host's shell is a login shell (udl.hosts.shell.remote_line), so a tool its user
        installs in ~/.local/bin is found, as it would be for the user themselves."""
        found: list[ToolStatus] = []
        for tool in TOOLS:
            try:
                result = await shell.run([tool, "--version"])
            except Exception:  # noqa: BLE001 - (not installed, or it will not say: not found)
                found.append(ToolStatus(name=tool, found=False))
                continue
            first = result.stdout.strip().splitlines()
            found.append(ToolStatus(name=tool, found=True, version=first[0] if first else None))
        return found
