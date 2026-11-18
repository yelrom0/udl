"""
---------------------
- test_host_ops.py -
---------------------

The host operations: which hosts there are (local is always one, and cannot be deleted), adding
and changing one, and checking one (it runs `uname` there and reports the tools the runners
need: git, gh, claude, opencode)
"""

# System Imports
import os
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import connection, registry
from udl.config import DBSettings, SecretsSettings, Settings
from udl.core.jobs import InstanceLock
from udl.core.service import UDLService
from udl.errors import DoesNotExist, NotAllowed
from udl.hosts import Host, HostTransport
from udl.hosts.models import HostCheck, HostInfo, HostRequest, HostsList, SetHostRequest
from udl.plugins.secrets.memory import MemorySecretStore


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    settings = Settings(
        download_dir=tmp_path / "downloads",
        db=DBSettings(connections=[connection(primary=True)]),
        secrets=SecretsSettings(store="memory"),
    )
    made = UDLService(
        settings,
        registry=registry(),
        secrets=MemorySecretStore(settings.secrets),
        lock=InstanceLock(tmp_path / "udl.lock"),
    )
    await made.start()
    try:
        yield made
    finally:
        await made.close()


def ssh_host(name: str = "thevault") -> Host:
    return Host(name=name, transport=HostTransport.SSH, ssh=name, work_dir="/srv/udl/work")


# ---------------------------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------------------------


async def test_the_local_host_is_always_there(service: UDLService) -> None:
    listed = await service.list_hosts(HostsList())

    assert [host.name for host in listed.hosts] == ["local"]
    assert listed.hosts[0].transport is HostTransport.LOCAL


async def test_a_host_is_added_and_listed(service: UDLService) -> None:
    added = await service.set_host(SetHostRequest(host=ssh_host()))

    assert isinstance(added, HostInfo)
    assert added.name == "thevault"
    listed = await service.list_hosts(HostsList())
    assert [host.name for host in listed.hosts] == ["local", "thevault"]


async def test_an_unknown_host_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist):
        await service.delete_host(HostRequest(host="nope"))


async def test_the_local_host_cannot_be_deleted(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match="local"):
        await service.delete_host(HostRequest(host="local"))


async def test_a_host_is_deleted(service: UDLService) -> None:
    await service.set_host(SetHostRequest(host=ssh_host()))

    deleted = await service.delete_host(HostRequest(host="thevault"))

    assert deleted.deleted is True
    assert [host.name for host in (await service.list_hosts(HostsList())).hosts] == ["local"]


async def test_deleting_a_host_that_is_not_there_says_so(service: UDLService) -> None:
    with pytest.raises(DoesNotExist):
        await service.delete_host(HostRequest(host="nope"))


async def test_a_host_is_changed_by_its_name(service: UDLService) -> None:
    await service.set_host(SetHostRequest(host=ssh_host()))

    changed = await service.set_host(
        SetHostRequest(host=ssh_host(), update={"work_dir": "/srv/other", "enabled": False})
    )

    assert changed.work_dir == "/srv/other"
    assert changed.enabled is False


async def test_a_change_to_a_field_a_host_has_not_is_refused(service: UDLService) -> None:
    await service.set_host(SetHostRequest(host=ssh_host()))

    with pytest.raises(NotAllowed, match="There is no such field"):
        await service.set_host(SetHostRequest(host=ssh_host(), update={"no_such": 1}))


async def test_a_host_cannot_be_changed_into_another_transport(service: UDLService) -> None:
    await service.set_host(SetHostRequest(host=ssh_host()))

    with pytest.raises(NotAllowed, match="is not this machine"):
        await service.set_host(
            SetHostRequest(host=ssh_host(), update={"transport": "local", "ssh": None})
        )


# ---------------------------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------------------------


async def test_checking_a_host_runs_uname_and_looks_for_the_tools(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (The suite runs with a PATH of the venv's own: put the system's back, as the ssh tests do,
    # so `uname` is found — a check's job is to say what a host has.)
    monkeypatch.setenv("PATH", f"{os.defpath}:/usr/local/bin:{os.environ.get('PATH', '')}")

    report = await service.check_host(HostRequest(host="local"))

    assert isinstance(report, HostCheck)
    assert report.host == "local"
    assert report.ok is True
    assert report.uname
    assert {tool.name for tool in report.tools} == {"git", "gh", "claude", "opencode"}
    assert all(isinstance(tool.found, bool) for tool in report.tools)


async def test_checking_a_host_that_is_not_there_says_so(service: UDLService) -> None:
    with pytest.raises(DoesNotExist):
        await service.check_host(HostRequest(host="nope"))
