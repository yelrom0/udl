"""
-------------------------------
- test_health_operations.py -
-------------------------------

A host's health over the app (27.13): what one check opens, says and raises, what a second check
leaves as it is, and what a host that recovers closes — with `act` holding its turns and gates,
`tell` raising the action alone, and `act_and_tell` (the default) doing both.
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

# Package Imports
import pytest

# Local Imports
from support.engine import settings
from support.orch import orch_registry
from udl.automations.events import EventRecord
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import NotAllowed
from udl.plugins.apps.base import AppEvents, AppRecords
from udl.plugins.apps.orch.actions import ActionRecords
from udl.plugins.apps.orch.events import (
    HOST_HEALTHY,
    HOST_UNHEALTHY,
    HostHealthy,
    HostUnhealthy,
)
from udl.plugins.apps.orch.health import Health, issues_of
from udl.plugins.apps.orch.models import (
    ActionState,
    HealthKind,
    HostHealth,
    HostHealthList,
    HostHealthRequest,
    Inbox,
    InboxRequest,
    PushRequest,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    HostIssue,
    HostIssueRecords,
    Project,
    ProjectRecords,
    Repo,
    RepoRecords,
    Task,
    TaskRecords,
)
from udl.plugins.apps.orch.settings import HealthSettings

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)

Breach = Literal["act_and_tell", "tell", "act"]


def _health(host: str = "jeff-wiggle", **overrides: object) -> HostHealth:
    """A host whose numbers are healthy, with `overrides` on top."""
    healthy: dict[str, object] = {
        "host": host,
        "memory_available": 8 * 1024**3,
        "swap_used": 0,
        "swap_total": 2 * 1024**3,
        "io_pressure": 1.0,
        "disks": [
            {"path": "/w", "filesystem": "ext4", "free": 500 * 1024**3, "size": 1000 * 1024**3}
        ],
        "at": NOW,
    }
    return HostHealth.model_validate({**healthy, **overrides})


class Reader:
    """What a host says about itself, as the check asks it: the health it was given, and how
    often it was asked."""

    def __init__(self, *health: HostHealth) -> None:
        self.health = list(health)
        self.asked: list[str] = []

    async def __call__(self, name: str) -> HostHealth:
        self.asked.append(name)
        return next((one for one in self.health if one.host == name), _health(name))


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service over a fresh database, with one project whose checkout is on the host."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", description="What udl is.", rules="No pushing.")
        )
        await RepoRecords(records).save_repo(
            Repo(
                project="udl",
                name="udl",
                github="yelrom0/udl",
                account="github:yelrom0",
                checkouts={"jeff-wiggle": "/home/yelrom0/Documents/code/udl"},
            )
        )
        await FeatureRecords(records).save_feature(
            Feature(project="udl", key="26", title="Work tracking", spec="Phase 26.", order=26)
        )
        for key, title in (("26.1", "Projects and repos"), ("26.2", "Features and tasks")):
            await TaskRecords(records).save_task(
                Task(project="udl", feature="26", key=key, title=title, spec="The card.")
            )
        await AgentRecords(records).save_agent(
            Agent(name="deepseek-a", title="deepseek-a", runner="fake", host="local")
        )
        yield running


def _records(service: UDLService) -> AppRecords:
    """`service`'s orch records, publishing where the app does."""
    return AppRecords(service.db, events=AppEvents(service.bus, "orch", Orch.event_types()))


def _health_of(
    service: UDLService, reader: Reader, *, on_breach: Breach = "act_and_tell"
) -> Health:
    """The check over `service`'s records, reading what `reader` says."""
    return Health(
        records=_records(service),
        db=service.db,
        clock=service.clock,
        settings=HealthSettings(on_breach=on_breach),
        reading=reader,
    )


async def _said(service: UDLService, kind: str) -> list[EventRecord]:
    """The events of `kind` stored so far, oldest first."""
    found = await service.db.list_records(EventRecord, RecordQuery(where={"type": kind}))
    return sorted(found, key=lambda one: one.occurred_at)


async def test_a_check_that_finds_a_broken_host_opens_an_issue_and_raises_one_action(
    service: UDLService,
) -> None:
    reader = Reader(_health(io_pressure=90.0))

    found = await _health_of(service, reader).check(["jeff-wiggle"])

    assert [issue.kind for issue in found[0].issues] == [HealthKind.IO]
    open_issues = await HostIssueRecords(_records(service)).open()
    assert [(one.host, one.issue) for one in open_issues] == [("jeff-wiggle", "io")]
    assert open_issues[0].action == "A-1"
    action = await ActionRecords(_records(service)).action("udl", "A-1")
    assert action is not None
    assert action.state is ActionState.OPEN
    assert "I/O pressure" in action.text


async def test_a_second_check_does_not_raise_the_action_again(service: UDLService) -> None:
    check = _health_of(service, Reader(_health(io_pressure=90.0)))
    await check.check(["jeff-wiggle"])

    await check.check(["jeff-wiggle"])

    open_issues = await HostIssueRecords(_records(service)).open()
    assert [one.action for one in open_issues] == ["A-1"]
    assert len(await ActionRecords(_records(service)).actions("udl")) == 1


async def test_a_check_that_finds_it_healthy_closes_the_issue_and_its_action(
    service: UDLService,
) -> None:
    await _health_of(service, Reader(_health(io_pressure=90.0))).check(["jeff-wiggle"])

    found = await _health_of(service, Reader(_health())).check(["jeff-wiggle"])

    assert found[0].issues == []
    assert await HostIssueRecords(_records(service)).open() == []
    action = await ActionRecords(_records(service)).action("udl", "A-1")
    assert action is not None
    assert action.state is ActionState.DONE
    assert "cleared at" in (action.result or "")


async def test_act_holds_a_host_while_it_has_an_issue_and_lets_it_go_when_it_clears(
    service: UDLService,
) -> None:
    check = _health_of(service, Reader(_health(io_pressure=90.0)), on_breach="act")
    assert await check.held("jeff-wiggle") is False

    await check.check(["jeff-wiggle"])
    assert await check.held("jeff-wiggle") is True

    await _health_of(service, Reader(_health()), on_breach="act").check(["jeff-wiggle"])
    assert await check.held("jeff-wiggle") is False


async def test_tell_raises_the_action_and_holds_nothing(service: UDLService) -> None:
    check = _health_of(service, Reader(_health(io_pressure=90.0)), on_breach="tell")

    await check.check(["jeff-wiggle"])

    assert await check.held("jeff-wiggle") is False
    assert len(await ActionRecords(_records(service)).actions("udl")) == 1


async def test_a_host_that_breaks_says_what_and_one_that_clears_says_so(
    service: UDLService,
) -> None:
    await _health_of(service, Reader(_health(io_pressure=90.0))).check(["jeff-wiggle"])
    await _health_of(service, Reader(_health())).check(["jeff-wiggle"])

    broken = await _said(service, HOST_UNHEALTHY.name)
    cleared = await _said(service, HOST_HEALTHY.name)
    assert len(broken) == 1
    said = HostUnhealthy.model_validate(broken[0].data)
    assert said.host == "jeff-wiggle"
    assert [(one.kind, one.host) for one in said.issues] == [("io", "jeff-wiggle")]
    assert "I/O pressure" in said.issues[0].detail
    assert len(cleared) == 1
    over = HostHealthy.model_validate(cleared[0].data)
    assert [one.kind for one in over.cleared] == ["io"]


async def test_a_breach_that_comes_back_is_said_again(service: UDLService) -> None:
    """(Event dedupe is unique on its type and key, so a key that is only the host and the kind
    would keep the second opening from ever being published.)"""
    broken = _health_of(service, Reader(_health(io_pressure=90.0)))

    await broken.check(["jeff-wiggle"])
    await _health_of(service, Reader(_health())).check(["jeff-wiggle"])
    await broken.check(["jeff-wiggle"])

    said = [
        HostUnhealthy.model_validate(one.data) for one in await _said(service, HOST_UNHEALTHY.name)
    ]
    assert [one.host for one in said] == ["jeff-wiggle", "jeff-wiggle"]


async def test_two_paths_on_one_mount_are_one_issue_and_two_mounts_are_said_in_one(
    service: UDLService,
) -> None:
    """(A `HostIssue` is one per host and kind: two paths on one mount are one disk, said once —
    and a balance runs once there — while two mounts are one issue that names each, so the
    balance names the one it needs.)"""
    one = _health(
        disks=[
            {
                "path": "/a",
                "mount": "/",
                "filesystem": "btrfs",
                "free": 500 * 1024**3,
                "size": 1000 * 1024**3,
                "unallocated": 1,
            },
            {
                "path": "/b",
                "mount": "/",
                "filesystem": "btrfs",
                "free": 500 * 1024**3,
                "size": 1000 * 1024**3,
                "unallocated": 1,
            },
        ]
    )
    (issue,) = issues_of(one, HealthSettings())
    assert issue.kind is HealthKind.BTRFS_UNALLOCATED
    assert "/b" not in issue.detail
    assert issue.hint.count("sudo btrfs balance") == 1

    # (the 23 Nov case: two filesystems that are both fully allocated say the same number)
    two = _health(
        disks=[
            {
                "path": "/a",
                "mount": "/",
                "filesystem": "btrfs",
                "free": 500 * 1024**3,
                "size": 1000 * 1024**3,
                "unallocated": 0,
            },
            {
                "path": "/b",
                "mount": "/data",
                "filesystem": "btrfs",
                "free": 500 * 1024**3,
                "size": 1000 * 1024**3,
                "unallocated": 0,
            },
        ]
    )
    (issue,) = issues_of(two, HealthSettings())
    assert issue.kind is HealthKind.BTRFS_UNALLOCATED
    assert "/a" in issue.detail
    assert "/b" in issue.detail
    assert "-dusage=10 /`" in issue.hint
    assert "-dusage=10 /data`" in issue.hint


async def test_a_host_with_no_checkout_raises_its_action_in_the_only_project(
    service: UDLService,
) -> None:
    # (An action lives in a project: a host whose checkout udl does not know is still this udl's
    # host, so its action goes in the only project there is.)
    check = _health_of(service, Reader(_health("somewhere-else", io_pressure=90.0)))

    found = await check.check(["somewhere-else"])

    assert [issue.kind for issue in found[0].issues] == [HealthKind.IO]
    open_issues = await HostIssueRecords(_records(service)).open()
    assert [one.host for one in open_issues] == ["somewhere-else"]
    assert open_issues[0].project == "udl"
    assert open_issues[0].action is not None


async def test_the_gates_it_reads_are_the_ones_its_lane_holds(service: UDLService) -> None:
    await service.db.claim_lease("orch-gate:jeff-wiggle:0", "someone", timedelta(minutes=5))

    found = await _health_of(service, Reader(_health())).check(["jeff-wiggle"])

    assert found[0].gates_running == 1
    assert found[0].gates_allowed == 1


def test_a_record_example_is_an_issue_that_can_be_stored() -> None:
    example = HostIssue.example()

    assert example.issue == "btrfs_unallocated"
    assert example.closed_at is None


async def test_the_operation_reads_every_host_and_records_nothing(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(It is read-only: the dispatcher's check is what opens issues and raises actions.)"""
    app = await service.apps.get("orch")
    assert isinstance(app, Orch)
    monitor = _health_of(service, Reader(_health("local", io_pressure=90.0)))

    def _monitor(_: UDLService) -> Health:
        return monitor

    monkeypatch.setattr(app, "monitor", _monitor)

    found = await app.host_health(HostHealthRequest(), service=service)

    assert isinstance(found, HostHealthList)
    assert [one.host for one in found.hosts] == ["local"]
    assert [issue.kind for issue in found.hosts[0].issues] == [HealthKind.IO]
    assert await HostIssueRecords(_records(service)).open() == []


async def test_the_inbox_says_a_host_is_held_while_it_is(
    service: UDLService,
) -> None:
    await _health_of(service, Reader(_health(io_pressure=90.0))).check(["jeff-wiggle"])
    app = await service.apps.get("orch")
    assert isinstance(app, Orch)

    inbox = await app.inbox(InboxRequest(), service=service)

    assert isinstance(inbox, Inbox)
    assert [one.name for one in inbox.hosts] == ["jeff-wiggle"]
    assert inbox.hosts[0].held is True
    assert "io" in inbox.hosts[0].line


async def test_a_push_on_a_held_host_is_refused_before_its_gate(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = await service.apps.get("orch")
    assert isinstance(app, Orch)
    monitor = _health_of(service, Reader(_health("local", io_pressure=90.0)), on_breach="act")

    def _monitor(_: UDLService) -> Health:
        return monitor

    monkeypatch.setattr(app, "monitor", _monitor)
    await TaskRecords(_records(service)).save_task(
        Task(
            project="udl",
            feature="26",
            key="26.3",
            title="Held",
            spec="The card.",
            assignee="deepseek-a",
            worktree="/work/26.3",
            branch="feat/26.3-held",
        )
    )

    with pytest.raises(NotAllowed) as refused:
        await app.push(PushRequest(project="udl", key="26.3"), service=service)

    assert "held" in refused.value.message
    assert "I/O pressure" in (refused.value.hint or "")
