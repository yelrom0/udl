"""
---------------------
- test_gate_caps.py -
---------------------

A gate under a memory cap, and one that runs past its time (27.12): a gate the kernel stops at
the cap, or one udl stops itself, pushes nothing and says which it was; the repo's cleanup runs
after either, and not after a gate that simply failed.

`push()` is called directly here, with a shell that hands back the gate's ending: the rest of the
push (the git it runs, the pull it opens) is the real thing, on a real repository.
"""

# System Imports
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import BaseModel

# Local Imports
from support.engine import registry, settings
from support.git import git
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import NotAllowed, ToolUnavailable
from udl.hosts import Host
from udl.hosts.shell import HostShell, Interaction, ResourceLimits
from udl.plugins.apps.base import AppPlugin, AppRecords, AppSettings
from udl.plugins.apps.orch.models import IN_PROGRESS
from udl.plugins.apps.orch.models import Principal as Who
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    Project,
    ProjectRecords,
    Repo,
    RepoRecords,
    Task,
    TaskRecords,
)
from udl.plugins.apps.orch.settings import OrchSettings
from udl.plugins.apps.orch.workspace import prepare, push
from udl.plugins.registry import APPS, PluginRegistry
from udl.process import CancelToken, ProcessResult

AGENT: str = "deepseek-b"


class OpenPull(BaseModel):
    """What `github_open_pull` is asked."""

    repo: str
    head: str
    title: str | None = None
    base: str | None = None
    body: str | None = None


class FakePull(BaseModel):
    """What `github_open_pull` answers, as a push reads it."""

    repo: str = "yelrom0/udl"
    number: int = 7
    title: str = "It"
    state: str = "open"
    draft: bool = False
    merged: bool = False
    head: str = "feat/F-1.1-it"
    head_sha: str = "abc1234"
    base: str = "main"
    html_url: str = "https://github.com/yelrom0/udl/pull/7"


class FakeGitHub(AppPlugin[AppSettings]):
    """A `github` app whose one operation answers a pull (what a push opens)."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]
        self.opened: list[dict[str, Any]] = []

    @classmethod
    def operations(cls) -> list[Any]:
        return [
            cls.operation("open_pull", title="Open a pull", hints=OperationHints(open_world=True))
        ]

    async def open_pull(self, request: OpenPull) -> FakePull:
        """Record the pull that was asked for, and answer it."""
        asked = request.model_dump()
        self.opened.append(asked)
        return FakePull(title=str(asked.get("title") or "It"))


class GateShell:
    """This machine's shell, with the gate's ending scripted: how a capped run ends on a host
    udl cannot make one on (a test machine with no user systemd, or another's kernel)."""

    def __init__(
        self,
        shell: HostShell,
        *,
        gate: ProcessResult | None = None,
        slow: bool = False,
        cleanup: Sequence[str] = ("sh", "-c", "cat", "cleanup.txt"),
        cleanup_says: str = "containers removed",
        enforces: bool = True,
        stop_fails: bool = False,
    ) -> None:
        self._shell = shell
        self.host = shell.host
        self._gate = gate
        self._slow = slow
        self._cleanup = list(cleanup)
        self._cleanup_says = cleanup_says
        self._enforces = enforces
        self._stop_fails = stop_fails
        self.units: list[str | None] = []
        """The unit each gate run was under."""
        self.limits: list[ResourceLimits | None] = []
        """The cap each gate run was given."""
        self.stops: list[list[str]] = []
        """Every argv that was not the gate (the stop, the cleanup), in order."""
        self.cleanups: list[list[str]] = []
        """Every cleanup argv, in order."""

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
        cancel: CancelToken | None = None,
        check: bool = True,
        tail: bool = False,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> ProcessResult:
        if list(argv) == ["systemd-run", "--user", "--scope", "-q", "true"]:
            # (The probe the gate makes before stopping a unit it is not sure exists.)
            return ProcessResult(returncode=0 if self._enforces else 127, stdout="", stderr="")
        if limits is not None:
            self.units.append(unit)
            self.limits.append(limits)
            if self._slow:
                raise TimeoutError
            assert self._gate is not None
            return self._gate
        if list(argv)[:3] == ["systemctl", "--user", "stop"]:
            self.stops.append(list(argv))
            if self._stop_fails:
                raise ToolUnavailable("systemctl is not installed here")
            return ProcessResult(returncode=0, stdout="", stderr="")
        if list(argv) == self._cleanup:
            self.cleanups.append(list(argv))
            return ProcessResult(returncode=0, stdout=self._cleanup_says, stderr="")
        return await self._shell.run(
            argv,
            cwd=cwd,
            env=env,
            secret_env=secret_env,
            stdin=stdin,
            within=within,
            cancel=cancel,
            check=check,
            tail=tail,
        )

    def stream(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> AsyncIterator[bytes]:
        return self._shell.stream(
            argv,
            cwd=cwd,
            env=env,
            secret_env=secret_env,
            stdin=stdin,
            within=within,
            limits=limits,
            unit=unit,
        )

    async def interact(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        within: timedelta | None = None,
    ) -> Interaction:
        return await self._shell.interact(
            argv, cwd=cwd, env=env, secret_env=secret_env, within=within
        )

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        return self._shell.forward(port)


def orch_registry() -> PluginRegistry:
    plugins = registry()
    plugins.register(APPS, FakeGitHub)
    plugins.register(APPS, Orch)
    return plugins


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A temp folder: the host's work dir, the bare `origin` and a checkout of it."""
    (tmp_path / "work").mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    bare = tmp_path / "origin.git"
    git("init", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("init", "-b", "main", cwd=checkout)
    (checkout / "README.md").write_text("udl\n")
    git("add", "README.md", cwd=checkout)
    git("config", "user.name", "udl", cwd=checkout)
    git("config", "user.email", "udl@example.com", cwd=checkout)
    git("config", "commit.gpgsign", "false", cwd=checkout)
    git("commit", "-m", "the first commit", cwd=checkout)
    git("remote", "add", "origin", str(bare), cwd=checkout)
    git("push", "-u", "origin", "main", cwd=checkout)
    return tmp_path


@pytest.fixture
async def service(tmp_path: Path, home: Path) -> AsyncIterator[UDLService]:
    """A service with the project, its repo, the agent, and the task in its worktree."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        await running.db.upsert_record(Host.local(str(home / "work")))
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        await RepoRecords(records).save_repo(
            Repo(
                project="udl",
                name="udl",
                github="yelrom0/udl",
                account="github:yelrom0",
                base_branch="main",
                gate=["true"],
                gate_cleanup=["sh", "-c", "cat", "cleanup.txt"],
                checkouts={"local": str(home / "checkout")},
            )
        )
        await AgentRecords(records).save_agent(
            Agent(name=AGENT, title=AGENT, runner="opencode", host="local")
        )
        tasks = TaskRecords(records)
        await tasks.save_task(
            Task(project="udl", feature="F-1", key="F-1.1", title="It", repo="udl", assignee=AGENT)
        )
        task = await tasks.task("udl", "F-1.1")
        assert task is not None
        await tasks.move(task, IN_PROGRESS, by=Who(kind="system", role="start"))
        started = await tasks.task("udl", "F-1.1")
        assert started is not None
        repo = await RepoRecords(records).repo("udl", "udl")
        assert repo is not None
        found = await prepare(
            started,
            repo=repo,
            host=Host.local(str(home / "work")),
            shell=await running.hosts.shell("local"),
        )
        await tasks.save_task(
            started.model_copy(update={"branch": found.branch, "worktree": found.path})
        )
        (Path(found.path) / "work.txt").write_text("the work\n")
        (Path(found.path) / "cleanup.txt").write_text("containers removed\n")
        git("add", "work.txt", "cleanup.txt", cwd=Path(found.path))
        git("commit", "-m", "feat: the work", cwd=Path(found.path))
        yield running


async def pushed(
    service: UDLService,
    shell: GateShell,
    *,
    memory: int | None = 4 * 1024**3,
) -> Any:
    """`push()`, with the gate's ending scripted."""
    records = AppRecords(service.db)
    task = await TaskRecords(records).task("udl", "F-1.1")
    assert task is not None
    repo = await RepoRecords(records).repo("udl", "udl")
    assert repo is not None
    return await push(
        task,
        repo=repo,
        host=Host.local(str(shell.host.work_dir)),
        shell=shell,
        service=service,
        records=records,
        title="It",
        body=None,
        within=timedelta(minutes=30),
        memory=memory,
        cleanup=repo.gate_cleanup,
    )


def a_gate(shell: HostShell, **kwargs: Any) -> GateShell:
    return GateShell(shell, **kwargs)


# ---------------------------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------------------------


async def test_a_gate_stopped_at_its_memory_cap_pushes_nothing_and_says_so(
    service: UDLService, home: Path
) -> None:
    """(The card's done-when: a gate stopped by its cap pushes nothing, and says `memory`.)"""
    shell = a_gate(
        await service.hosts.shell("local"),
        gate=ProcessResult(returncode=137, stdout="", stderr="", limits="enforced"),
    )

    with pytest.raises(NotAllowed, match="the gate passed its memory cap of 4 GiB") as refused:
        await pushed(service, shell)

    hint = refused.value.hint or ""
    assert hint.startswith("stopped: memory")
    assert "containers removed" in hint
    assert shell.limits[0] == ResourceLimits(memory=4 * 1024**3)
    assert (
        git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=home / "origin.git")
        == "main"
    )
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert app.opened == []


async def test_a_gate_that_runs_past_its_time_is_stopped_and_says_so(
    service: UDLService, home: Path
) -> None:
    """(A timeout stops the unit, so the suite's children go too, and never reads as a cap.)"""
    shell = a_gate(await service.hosts.shell("local"), slow=True)

    with pytest.raises(NotAllowed, match="ran past the 0:30:00 it gets") as refused:
        await pushed(service, shell)

    assert (refused.value.hint or "").startswith("stopped: timeout")

    unit = shell.units[0]
    assert unit is not None
    assert shell.stops == [["systemctl", "--user", "stop", f"{unit}.scope"]]
    assert len(shell.cleanups) == 1
    assert (
        git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=home / "origin.git")
        == "main"
    )


async def test_a_timeout_where_the_host_caps_nothing_stops_nothing(
    service: UDLService,
) -> None:
    """(A host with no user systemd never made a scope, so there is no unit to stop: the gate
    was the plain program, and the cleanup still runs.)"""
    shell = a_gate(await service.hosts.shell("local"), slow=True, enforces=False)

    with pytest.raises(NotAllowed, match="ran past the 0:30:00 it gets") as refused:
        await pushed(service, shell)

    assert (refused.value.hint or "").startswith("stopped: timeout")
    assert shell.stops == []
    assert len(shell.cleanups) == 1


async def test_a_stop_that_fails_is_said_and_the_cleanup_still_runs(
    service: UDLService,
) -> None:
    """(A gate over its time on a host whose stop does not work is still the agent's refusal,
    with what went wrong said, and the cleanup runs whatever the stop did.)"""
    shell = a_gate(await service.hosts.shell("local"), slow=True, stop_fails=True)

    with pytest.raises(NotAllowed, match="ran past the 0:30:00 it gets") as refused:
        await pushed(service, shell)

    hint = refused.value.hint or ""
    assert hint.startswith("stopped: timeout")
    assert "systemctl is not installed here" in hint
    assert len(shell.cleanups) == 1


async def test_the_cleanup_runs_only_after_a_stop(service: UDLService) -> None:
    """(A gate that simply failed leaves what it made: the agent reads its failure where it is.)"""
    shell = a_gate(
        await service.hosts.shell("local"),
        gate=ProcessResult(returncode=5, stdout="the gate says no", stderr="", limits="enforced"),
    )

    with pytest.raises(NotAllowed, match="the gate failed with exit code 5"):
        await pushed(service, shell)

    assert shell.cleanups == []


async def test_a_gate_that_ends_on_its_own_is_not_a_stop(service: UDLService) -> None:
    """(137 without a cap the host enforces is the suite's own business: udl only knows what it
    asked for.)"""
    shell = a_gate(
        await service.hosts.shell("local"),
        gate=ProcessResult(returncode=137, stdout="killed", stderr="", limits="unenforced"),
    )

    with pytest.raises(NotAllowed, match="exit code 137"):
        await pushed(service, shell)

    assert shell.cleanups == []


async def test_the_gate_s_unit_is_named_for_the_task_and_escaped(service: UDLService) -> None:
    shell = a_gate(
        await service.hosts.shell("local"),
        gate=ProcessResult(returncode=0, stdout="", stderr="", limits="enforced"),
    )

    await pushed(service, shell)

    unit = shell.units[0]
    assert unit is not None
    assert unit.startswith("udl-gate-udl-F-1.1-")
    assert len(unit.rsplit("-", 1)[1]) == 8


async def test_no_cap_is_a_plain_run(service: UDLService) -> None:
    shell = a_gate(
        await service.hosts.shell("local"),
        gate=ProcessResult(returncode=0, stdout="", stderr=""),
    )

    await pushed(service, shell, memory=None)

    assert shell.limits == []


# ---------------------------------------------------------------------------------------------
# The settings
# ---------------------------------------------------------------------------------------------


def test_the_caps_have_their_defaults() -> None:
    found = OrchSettings()

    assert found.gates_per_host == 1
    assert found.gate_memory == 4 * 1024**3
    assert found.session_memory == 20 * 1024**3


def test_a_cap_can_be_written_as_a_size_or_turned_off() -> None:
    assert OrchSettings.model_validate({"gate_memory": "2GiB"}).gate_memory == 2 * 1024**3
    assert OrchSettings(gate_memory=None).gate_memory is None
    assert OrchSettings(session_memory=None).session_memory is None
