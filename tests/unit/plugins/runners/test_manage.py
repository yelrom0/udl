"""
-------------------
- test_manage.py -
-------------------

Managing harnesses on every host (25.11): what each runner is and whether it is signed in, its
install (its own fixed argv, a version checked first, never while a session runs), its setup (the
deny policies and the agent files), a sign-in relayed end to end and ended after ten minutes, and a
sign-out that names what it signs out.

Every test drives `Harnesses` on a real service whose hosts answer through a `ScriptedShell`, so
nothing here runs a harness: the tests say what it prints.
"""

# System Imports
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.cli import CLI
from support.clock import FakeClock
from support.engine import settings
from support.harnesses import ScriptedShell
from support.interfaces import Harness
from udl.core.operations import Operation, OperationRegistry
from udl.core.service import UDLService
from udl.errors import DoesNotExist, NotAllowed, PluginError, ToolUnavailable
from udl.hosts.shell import Host, HostTransport
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.registry import APPS, PluginRegistry
from udl.plugins.runners.base import AnAgentRunner, RunnerHealth, RunnerModel
from udl.plugins.runners.manage import (
    TAIL,
    Harnesses,
    InstallRunnerRequest,
    ListRunnersRequest,
    LoginReplyRequest,
    LoginStatusRequest,
    LogoutRunnerRequest,
    SetupRunnerRequest,
    StartLoginRequest,
)

HOSTS: tuple[str, ...] = ("local", "thevault")
"""The hosts every test's service has."""

CLAUDE_STATUS: str = '{"loggedIn": true, "email": "me@example.com", "subscriptionType": "max"}'
"""What `claude auth status` prints."""

OPENCODE_AUTHS: str = '[{"provider": "openrouter", "account": "me@example.com"}]'
"""What `opencode auth list --format json` prints."""

LIMIT: datetime = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
"""When a quota is back, in a test."""


class Hosts:
    """A started service with two hosts, and the shell each answers through."""

    def __init__(self, service: UDLService, shells: dict[str, ScriptedShell]) -> None:
        self.service = service
        self.shells = shells

    @property
    def harnesses(self) -> Harnesses:
        return Harnesses(self.service)

    def shell(self, host: str) -> ScriptedShell:
        return self.shells[host]


def host(name: str) -> Host:
    """One of the two hosts every test has."""
    if name == "local":
        return Host.local("/w")
    return Host(name=name, transport=HostTransport.SSH, ssh=name, work_dir="/w")


@pytest.fixture
async def hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Hosts]:
    """A started service with `local` and `thevault`, each answering through a script."""
    shells = {name: ScriptedShell(host(name)) for name in HOSTS}

    def shell_for(found: Host) -> ScriptedShell:
        return shells[found.name]

    monkeypatch.setattr("udl.hosts.manager.shell_for", shell_for)
    service = UDLService(
        settings(tmp_path, integrations={"command": {"argv": ["my-harness", "--chat"]}}),
        registry=PluginRegistry.discover(builtin_only=True),
        clock=FakeClock(),
    )
    async with service:
        for name in HOSTS:
            await service.db.upsert_record(host(name))
        yield Hosts(service, shells)


def script_claude(shell: ScriptedShell, *, version: str = "2.0.1 (Claude Code)") -> None:
    """A host whose `claude` answers as a signed-in one."""
    shell.answer(["claude", "--version"], version)
    shell.answer(["claude", "auth", "status"], CLAUDE_STATUS)
    shell.answer(["claude", "doctor"], "All good\n")


def script_opencode(shell: ScriptedShell, *, version: str = "2.1.4") -> None:
    """A host whose `opencode` is installed and signed in, with the deny policies in place."""
    shell.answer(["opencode", "--version"], version)
    shell.answer(["opencode", "auth", "list", "--format", "json"], OPENCODE_AUTHS)


# ---------------------------------------------------------------------------------------------
# What each harness is
# ---------------------------------------------------------------------------------------------


async def test_every_host_s_harnesses_are_listed(hosts: Hosts) -> None:
    for name in HOSTS:
        script_claude(hosts.shell(name))

    listed = await hosts.harnesses.all(ListRunnersRequest())

    claude = [
        item for item in listed.items if item.runner == "claude-code" and item.host == "local"
    ]
    assert len(claude) == 1
    health = claude[0].health
    assert health.installed is True
    assert health.version == "2.0.1 (Claude Code)"
    assert health.signed_in is True
    assert health.account == "me@example.com"
    assert health.plan == "max"
    assert health.guards is True  # (its deny list is a settings file per session)
    assert health.problems == []
    # (opencode was not scripted, so it cannot be asked: its row stays, saying so.)
    opencode = [item for item in listed.items if item.runner == "opencode" and item.host == "local"]
    assert len(opencode) == 1
    assert opencode[0].health.problems
    assert listed.problems == []


async def test_a_harness_that_is_not_there_says_so(hosts: Hosts) -> None:
    listed = await hosts.harnesses.all(ListRunnersRequest(host="local"))

    assert [item.health.installed for item in listed.items if item.runner == "claude-code"] == [
        False
    ]


async def test_only_one_host_s_harnesses_are_listed(hosts: Hosts) -> None:
    for name in HOSTS:
        script_claude(hosts.shell(name))

    listed = await hosts.harnesses.all(ListRunnersRequest(host="thevault"))

    assert {item.host for item in listed.items} == {"thevault"}


async def test_an_unknown_host_is_refused(hosts: Hosts) -> None:
    with pytest.raises(DoesNotExist):
        await hosts.harnesses.all(ListRunnersRequest(host="nowhere"))


async def test_a_harness_that_cannot_be_asked_keeps_its_row(hosts: Hosts) -> None:
    """opencode is there but its service is not: its row stays, and its health says what could
    not be asked — a listing that dropped it would hide a harness that is installed."""
    shell = hosts.shell("local")
    shell.answer(["opencode", "--version"], "2.1.4")

    listed = await hosts.harnesses.all(ListRunnersRequest(host="local"))

    [opencode] = [item for item in listed.items if item.runner == "opencode"]
    assert opencode.health.installed is True
    assert opencode.models == []
    assert any("models" in problem for problem in opencode.health.problems)
    assert listed.problems == []


async def test_a_quota_is_what_the_dispatcher_saw(hosts: Hosts) -> None:
    script_claude(hosts.shell("local"))
    harnesses = hosts.harnesses
    harnesses.limited("local", "claude-code", LIMIT)

    listed = await harnesses.all(ListRunnersRequest(host="local"))

    [claude] = [item for item in listed.items if item.runner == "claude-code"]
    assert claude.health.limited_until == LIMIT


# ---------------------------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------------------------


async def test_an_install_runs_the_runner_s_own_argv(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_claude(shell)
    shell.answer(["claude", "install", "2.0.2"], "installed 2.0.2\n")
    shell.answer(["claude", "--version"], "2.0.2 (Claude Code)")

    installed = await hosts.harnesses.install(
        InstallRunnerRequest(host="local", runner="claude-code", version="2.0.2")
    )

    assert ["claude", "install", "2.0.2"] in shell.runs
    assert installed.version == "2.0.2 (Claude Code)"
    assert "installed 2.0.2" in installed.output


async def test_an_update_without_a_version_updates(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_claude(shell)

    await hosts.harnesses.install(InstallRunnerRequest(host="local", runner="claude-code"))

    assert ["claude", "update"] in shell.runs


async def test_opencode_s_install_restarts_its_service(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_opencode(shell)

    await hosts.harnesses.install(
        InstallRunnerRequest(host="local", runner="opencode", version="2.1.5")
    )

    assert ["opencode", "upgrade", "2.1.5"] in shell.runs
    assert ["opencode", "service", "restart"] in shell.runs


@pytest.mark.parametrize(
    "version", ["1.2; rm -rf /", "--help", "--version", "-rf", "-x", "--", "1.2 3", ""]
)
async def test_a_bad_version_is_refused_before_anything_runs(hosts: Hosts, version: str) -> None:
    """A version is letters, digits, dots and dashes, and it starts with one of the first two: a
    leading dash is an option to the harness's own program, never a version."""
    shell = hosts.shell("local")
    script_claude(shell)

    with pytest.raises(NotAllowed):
        await hosts.harnesses.install(
            InstallRunnerRequest(host="local", runner="claude-code", version=version)
        )

    assert shell.runs == []


async def test_an_update_is_refused_while_a_session_runs(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_claude(shell)
    harnesses = hosts.harnesses
    harnesses.started("local", "claude-code", "s-1")

    with pytest.raises(NotAllowed):
        await harnesses.install(InstallRunnerRequest(host="local", runner="claude-code"))

    assert harnesses.running("local", "claude-code") == 1
    harnesses.ended("local", "claude-code", "s-1")
    assert harnesses.running("local", "claude-code") == 0
    await harnesses.install(InstallRunnerRequest(host="local", runner="claude-code"))
    assert ["claude", "update"] in shell.runs


async def test_an_install_s_output_is_kept_by_characters(hosts: Hosts) -> None:
    """A long install streams thousands of chunks: the tail is the end of what it said, not the
    last few chunks of it."""
    shell = hosts.shell("local")
    script_claude(shell)
    said = "x" * (TAIL * 2) + "done\n"
    shell.answer(["claude", "install", "2.0.2"], said)

    installed = await hosts.harnesses.install(
        InstallRunnerRequest(host="local", runner="claude-code", version="2.0.2")
    )

    assert len(installed.output) == TAIL
    assert installed.output.endswith("done\n")


async def test_a_runner_udl_cannot_make_is_a_problem_not_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runner whose settings do not fit it is listed as a problem, and the others are still
    listed."""
    shells = {name: ScriptedShell(host(name)) for name in HOSTS}

    def shell_for(found: Host) -> ScriptedShell:
        return shells[found.name]

    monkeypatch.setattr("udl.hosts.manager.shell_for", shell_for)
    service = UDLService(
        settings(tmp_path, integrations={"claude-code": {"no_such_option": 1}}),
        registry=PluginRegistry.discover(builtin_only=True),
        clock=FakeClock(),
    )
    async with service:
        await service.db.upsert_record(host("local"))

        listed = await Harnesses(service).all(ListRunnersRequest(host="local"))

    [claude] = [item for item in listed.items if item.runner == "claude-code"]
    assert claude.health.installed is False
    assert any("no_such_option" in problem for problem in claude.health.problems)
    assert listed.problems == []
    assert any(item.runner == "command" for item in listed.items)


async def test_a_runner_that_cannot_answer_is_a_problem_in_its_row(
    hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`models()` may raise ToolUnavailable (opencode's API is not up): the row stays, with its
    health's problems saying what failed — not a crash, and not a gap in the listing."""
    script_claude(hosts.shell("local"))
    runner = await hosts.harnesses.runner("local", "claude-code")

    async def unavailable(_: AnAgentRunner) -> list[RunnerModel]:
        raise ToolUnavailable("its service is not running")

    monkeypatch.setattr(type(runner), "models", unavailable)

    listed = await hosts.harnesses.all(ListRunnersRequest(host="local"))

    [claude] = [item for item in listed.items if item.runner == "claude-code"]
    assert claude.models == []
    assert any("its service is not running" in problem for problem in claude.health.problems)
    assert listed.problems == []


async def test_a_runner_whose_health_fails_keeps_its_row(
    hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A health check that cannot answer at all is the row's problem, not a vanished harness."""
    script_claude(hosts.shell("local"))
    runner = await hosts.harnesses.runner("local", "claude-code")

    async def broken(_: AnAgentRunner) -> RunnerHealth:
        raise PluginError("its doctor crashed")

    monkeypatch.setattr(type(runner), "health", broken)

    listed = await hosts.harnesses.all(ListRunnersRequest(host="local"))

    [claude] = [item for item in listed.items if item.runner == "claude-code"]
    assert claude.health.installed is False
    assert any("its doctor crashed" in problem for problem in claude.health.problems)
    assert listed.problems == []


# ---------------------------------------------------------------------------------------------
# Setting up
# ---------------------------------------------------------------------------------------------


async def test_a_setup_writes_the_policies_and_the_agent_files(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_opencode(shell)

    setup = await hosts.harnesses.setup(
        SetupRunnerRequest(host="local", runner="opencode"), roles=["implementer", "reviewer"]
    )

    policies = shell.files["$HOME/.config/opencode/opencode.json"]
    assert "shell:git push *" in policies
    assert "shell:gh pr merge *" in policies
    assert set(setup.files) == {
        "$HOME/.config/opencode/opencode.json",
        "$HOME/.config/opencode/agents/udl-implementer.md",
        "$HOME/.config/opencode/agents/udl-reviewer.md",
    }
    assert (
        "udl's implementer agent" in shell.files["$HOME/.config/opencode/agents/udl-implementer.md"]
    )


async def test_a_setup_keeps_a_provider_key_out_of_every_argv(hosts: Hosts) -> None:
    """A key goes to the service's own credential store, so no command on the host carries it: not
    even when the service cannot be reached and the setup fails."""
    shell = hosts.shell("local")
    script_opencode(shell)

    with pytest.raises(ToolUnavailable, match="service is not running"):
        await hosts.harnesses.setup(
            SetupRunnerRequest(host="local", runner="opencode"),
            env={"OPENROUTER_API_KEY": "sk-secret"},
        )

    assert shell.runs  # (the files were written before the key was tried)
    assert all("sk-secret" not in " ".join(argv) for argv in shell.runs)
    assert all(not secrets for secrets in shell.secret_envs)  # (no key is handed to a command)


async def test_a_harness_that_enforces_its_own_guards_needs_no_files(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_claude(shell)

    setup = await hosts.harnesses.setup(
        SetupRunnerRequest(host="local", runner="claude-code"), roles=["implementer"]
    )

    assert setup.files == []
    assert setup.guards is True


async def test_a_setup_says_whether_the_guards_are_in_place(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_opencode(shell)

    before = await hosts.harnesses.setup(SetupRunnerRequest(host="local", runner="opencode"))

    assert before.guards is True  # (the file it wrote is what `guards()` reads)


# ---------------------------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------------------------


async def test_a_sign_in_is_relayed_end_to_end(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_claude(shell)
    shell.says = ("Open https://claude.ai/oauth?code=abc and paste the code\n",)

    harnesses = hosts.harnesses  # (one manager: an attempt outlives the call that started it)
    started = await harnesses.login(StartLoginRequest(host="local", runner="claude-code"))

    assert shell.starts == [["claude", "auth", "login"]]
    assert "https://claude.ai/oauth" in started.prompt
    assert started.expects == "code"
    assert started.done is False
    answered = await harnesses.reply(LoginReplyRequest(id=started.id, text="abc-123"))
    assert shell.interactions[0].written == ["abc-123"]
    assert answered.done is False  # (it is still waiting for the harness to end)
    await asyncio.sleep(0)  # (the watcher ends it: the script's exit code is 0)
    await asyncio.sleep(0)
    status = await harnesses.status(LoginStatusRequest(id=started.id))
    assert status.attempt.done is True
    assert status.attempt.ok is True


async def test_a_sign_in_that_is_not_driven_says_the_command(hosts: Hosts) -> None:
    """A CLI udl does not know signs in its own way: the answer is the exact command, not a
    failure (and not a substring of the runner's name)."""
    started = await hosts.harnesses.login(StartLoginRequest(host="local", runner="command"))

    assert started.done is True
    assert started.ok is False
    assert started.command == "my-harness"
    assert "my-harness" in started.prompt


async def test_one_sign_in_runs_per_host_and_runner(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_claude(shell)
    shell.says = ("Open https://claude.ai/oauth and paste the code\n",)
    harnesses = hosts.harnesses
    await harnesses.login(StartLoginRequest(host="local", runner="claude-code"))

    with pytest.raises(NotAllowed):
        await harnesses.login(StartLoginRequest(host="local", runner="claude-code"))

    await harnesses.stop()


async def test_a_sign_in_that_times_out_is_ended(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_claude(shell)
    shell.says = ("Open https://claude.ai/oauth and paste the code\n",)
    harnesses = hosts.harnesses
    started = await harnesses.login(StartLoginRequest(host="local", runner="claude-code"))
    clock = hosts.service.clock
    assert isinstance(clock, FakeClock)

    await clock.advance(timedelta(minutes=11))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert shell.interactions[0].closed is True
    status = await harnesses.status(LoginStatusRequest(id=started.id))
    assert status.attempt.done is True
    assert status.attempt.ok is False


async def test_a_finished_attempt_is_forgotten_when_the_next_starts(hosts: Hosts) -> None:
    """An attempt ends after ten minutes, and is forgotten: a host can be signed in again
    without the ended attempt holding its place."""
    shell = hosts.shell("local")
    script_claude(shell)
    harnesses = hosts.harnesses
    first = await harnesses.login(StartLoginRequest(host="local", runner="claude-code"))
    shell.interactions[0].exit_code = 0
    await shell.interactions[0].close()  # (the script ends)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert (await harnesses.status(LoginStatusRequest(id=first.id))).attempt.done is True

    second = await harnesses.login(StartLoginRequest(host="local", runner="claude-code"))

    assert second.id != first.id
    with pytest.raises(DoesNotExist):
        await harnesses.status(LoginStatusRequest(id=first.id))


@pytest.mark.parametrize(
    "said",
    [
        "Claude Code is ready. No errors found.\n",
        "Claude Code \u2014 sign in here:\n",
        "opencode 2.1.4: signed in.\n",
    ],
)
async def test_a_prompt_that_names_the_harness_is_not_asking_for_a_code(
    hosts: Hosts, said: str
) -> None:
    """`opencode` and `Claude Code` are not a code: only a harness asking for one says so."""
    shell = hosts.shell("local")
    script_claude(shell)
    shell.says = (said,)

    started = await hosts.harnesses.login(StartLoginRequest(host="local", runner="claude-code"))

    assert started.expects == "none"
    await hosts.harnesses.stop()


@pytest.mark.parametrize(
    "said",
    [
        "Paste the code here:\n",
        "Enter code: ",
        "your code: ",
        "Code: ",
    ],
)
async def test_a_harness_asking_for_a_code_says_so(hosts: Hosts, said: str) -> None:
    shell = hosts.shell("local")
    script_claude(shell)
    shell.says = (said,)

    started = await hosts.harnesses.login(StartLoginRequest(host="local", runner="claude-code"))

    assert started.expects == "code"
    await hosts.harnesses.stop()


async def test_an_unknown_sign_in_is_refused(hosts: Hosts) -> None:
    with pytest.raises(DoesNotExist):
        await hosts.harnesses.status(LoginStatusRequest(id="nope"))


# ---------------------------------------------------------------------------------------------
# Signing out
# ---------------------------------------------------------------------------------------------


async def test_a_sign_out_needs_the_host_and_runner(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_claude(shell)
    harnesses = hosts.harnesses

    with pytest.raises(NotAllowed):
        await harnesses.logout(
            LogoutRunnerRequest(host="local", runner="claude-code", confirm="local")
        )
    assert ["claude", "auth", "logout"] not in shell.runs

    signed_out = await harnesses.logout(
        LogoutRunnerRequest(host="local", runner="claude-code", confirm="local/claude-code")
    )
    assert signed_out.signed_out is True
    assert ["claude", "auth", "logout"] in shell.runs


async def test_opencode_s_sign_out_names_every_account(hosts: Hosts) -> None:
    shell = hosts.shell("local")
    script_opencode(shell)

    await hosts.harnesses.logout(
        LogoutRunnerRequest(host="local", runner="opencode", confirm="local/opencode")
    )

    assert ["opencode", "auth", "logout", "openrouter", "me@example.com"] in shell.runs


# ---------------------------------------------------------------------------------------------
# The operations orch offers
# ---------------------------------------------------------------------------------------------


def orch_operations() -> dict[str, Operation]:
    """The operations orch adds, as the registry offers them (by their full names)."""
    plugins = PluginRegistry()
    plugins.register(APPS, Orch)
    found = OperationRegistry.discover(plugins)
    return {operation.name: operation for operation in found.in_namespace("orch")}


def test_orch_offers_the_harness_operations() -> None:
    offered = list(orch_operations())
    assert offered[-7:] == [
        "orch_list_runners",
        "orch_runner_install",
        "orch_runner_setup",
        "orch_runner_login",
        "orch_runner_login_reply",
        "orch_runner_login_status",
        "orch_runner_logout",
    ]  # (the queue's and the token's come before them)


def test_a_harness_s_operations_are_udl_orch_runners_commands() -> None:
    paths = {
        name: operation.cli.path if operation.cli is not None else None
        for name, operation in orch_operations().items()
    }

    assert paths["orch_list_runners"] == ("orch", "runners")
    assert paths["orch_runner_install"] == ("orch", "runners", "install")
    assert paths["orch_runner_logout"] == ("orch", "runners", "logout")


def test_the_runners_command_lists_them_rather_than_showing_its_group_help(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`udl orch runners` and `udl orch runners --host local` both print the table: the listing
    sits at the group's own path, which is also where its install, setup and sign-in live."""
    shells = {name: ScriptedShell(host(name)) for name in HOSTS}

    def shell_for(found: Host) -> ScriptedShell:
        return shells[found.name]

    monkeypatch.setattr("udl.hosts.manager.shell_for", shell_for)
    script_claude(shells["local"])
    harness = Harness(tmp_path)
    harness.registry = PluginRegistry.discover(builtin_only=True)
    harness.registry.register(APPS, Orch)

    async def seed() -> None:
        async with harness.service() as service:
            for name in HOSTS:
                await service.db.upsert_record(host(name))

    asyncio.run(seed())

    everyone = CLI(harness)("orch", "runners")
    local = CLI(harness)("orch", "runners", "--host", "local")
    local_json = CLI(harness)("orch", "runners", "--host", "local", "--json")

    for listed_table in (everyone, local):
        assert listed_table.exit_code == 0, listed_table.output
        assert "Usage:" not in listed_table.stdout  # (it is the listing, not the group's help)
        assert "claude-code" in listed_table.stdout
    assert {name for name in HOSTS if name in everyone.stdout} == set(HOSTS)
    assert {name for name in HOSTS if name in local.stdout} == {"local"}
    assert local_json.exit_code == 0, local_json.output
    listed = json.loads(local_json.stdout)
    assert {item["host"] for item in listed["items"]} == {"local"}
    assert {item["runner"] for item in listed["items"]} >= {"claude-code", "command", "opencode"}
    assert listed["problems"] == []


def test_a_sign_in_is_never_an_mcp_tool() -> None:
    hints = {name: operation.hints for name, operation in orch_operations().items()}

    assert hints["orch_runner_login"].mcp is False
    assert hints["orch_runner_login_reply"].mcp is False
    assert hints["orch_runner_logout"].mcp is True
    assert hints["orch_runner_logout"].destructive is True
