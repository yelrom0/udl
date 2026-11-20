"""
---------------
- manage.py -
---------------

Managing harnesses on every host (25.11): what each one is and whether it is signed in
(orch_list_runners), installing or updating one, writing the deny policies and the agent files
(orch_runner_setup), signing one in (relayed to the person, one attempt per host and runner) and
signing it out.

Nothing here runs a command on a host that a runner did not name: an install is one of a runner's
own fixed argvs, and a version is checked against VERSION before anything runs. A runner is made
from its settings, on the host's shell, as the dispatcher (27.7) makes it: udl never learns whether
the program is here or on another machine.
"""

# System Imports
import asyncio
import re
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Literal
from uuid import uuid4

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

# Local Imports
from udl.errors import ConfigError, DoesNotExist, NotAllowed, PluginError, ToolUnavailable
from udl.hosts.shell import DEFAULT_TIMEOUT
from udl.plugins.registry import RUNNERS
from udl.plugins.runners.base import AnAgentRunner, LoginByHand, RunnerHealth, RunnerModel

if TYPE_CHECKING:
    from udl.core.service import UDLService
    from udl.hosts.shell import Interaction

__all__ = [
    "Harnesses",
    "InstallRunnerRequest",
    "ListRunnersRequest",
    "LoginAttempt",
    "LoginReplyRequest",
    "LoginStatus",
    "LoginStatusRequest",
    "LogoutRunnerRequest",
    "RunnerInstalled",
    "RunnerSetup",
    "RunnerStatus",
    "RunnerStatusList",
    "SetupRunnerRequest",
    "StartLoginRequest",
]

VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.\-]*$")
"""What a version may be (`latest`, `2.1.4`): letters, digits, dots and dashes, and it starts with a
letter or a digit — a leading dash would be an option to the harness's own program, never a
version. Anything else is refused before a command runs."""

TAIL: Final = 4_000
"""How much of an install's output is kept: its end, in characters, where a problem shows."""

LOGIN_TIMEOUT = timedelta(minutes=10)
"""How long a relayed sign-in gets before it is ended."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


# ---------------------------------------------------------------------------------------------
# What the operations take and answer
# ---------------------------------------------------------------------------------------------


class ListRunnersRequest(_Model):
    """Which harnesses to show."""

    host: str | None = None
    """Only this host's (None: every host)."""


class RunnerStatus(_Model):
    """One harness on one host: what it is, whether it is signed in, and what it can run."""

    host: str
    """The host it is on."""
    runner: str
    """The runner's name (`claude-code`, `opencode`, …)."""
    health: RunnerHealth
    """Whether it is there, its version and account, and what is wrong."""
    models: list[RunnerModel] = Field(default_factory=lambda: list[RunnerModel]())
    """What it can run there (empty: it could not say)."""


class RunnerStatusList(_Model):
    """Every harness on every host."""

    items: list[RunnerStatus] = Field(default_factory=lambda: list[RunnerStatus]())
    """One per (host, runner), by host then runner."""
    problems: list[str] = Field(default_factory=lambda: list[str]())
    """The (host, runner) pairs that could not be asked, and why."""


class InstallRunnerRequest(_Model):
    """Install or update a harness."""

    host: str
    """The host to install it on."""
    runner: str
    """The runner's name."""
    version: str | None = None
    """The version to install (None: the newest)."""


class RunnerInstalled(_Model):
    """What installing one did."""

    host: str
    """The host it was done on."""
    runner: str
    """The runner's name."""
    version: str | None = None
    """The version it is now, when it says."""
    output: str = ""
    """The end of what the install printed."""


class SetupRunnerRequest(_Model):
    """Write a harness's policies and agent files on a host."""

    host: str
    """The host to set up."""
    runner: str
    """The runner's name."""


class RunnerSetup(_Model):
    """What setting one up wrote."""

    host: str
    """The host it was done on."""
    runner: str
    """The runner's name."""
    guards: bool
    """Whether the deny policies are in place afterwards."""
    files: list[str] = Field(default_factory=lambda: list[str]())
    """The files it wrote, in the order it wrote them."""


class StartLoginRequest(_Model):
    """Sign a harness in."""

    host: str
    """The host it is on."""
    runner: str
    """The runner's name."""
    provider: str | None = None
    """The provider to sign in to (an OAuth one), when the runner has several."""


class LoginAttempt(_Model):
    """A sign-in that is running, and what it asks for."""

    id: str
    """What a reply and a status name it by."""
    host: str
    """The host it is on."""
    runner: str
    """The runner's name."""
    prompt: str = ""
    """What the harness printed: the text, and the URL to open."""
    expects: Literal["none", "code"] = "none"
    """What it wants back: nothing, or a code."""
    done: bool = False
    """Whether it has ended."""
    ok: bool | None = None
    """Whether it ended signed in (None: it has not ended)."""
    command: str | None = None
    """The command to run on the host, when the harness cannot be driven without a terminal."""


class LoginReplyRequest(_Model):
    """Give a running sign-in what it asked for."""

    id: str
    """The attempt's id."""
    text: str
    """What the person typed (a code, or a line)."""


class LoginStatusRequest(_Model):
    """Ask how a sign-in is going."""

    id: str
    """The attempt's id."""


class LoginStatus(_Model):
    """How a sign-in is going."""

    attempt: LoginAttempt
    """The attempt as it is now."""


class LogoutRunnerRequest(_Model):
    """Sign a harness out."""

    host: str
    """The host it is on."""
    runner: str
    """The runner's name."""
    confirm: str
    """`<host>/<runner>`, so a sign-out is never a typo away."""


class RunnerSignedOut(_Model):
    """What signing one out did."""

    host: str
    """The host it was done on."""
    runner: str
    """The runner's name."""
    signed_out: bool = True
    """Whether it is signed out now."""


# ---------------------------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------------------------


class _Attempt:
    """One sign-in that is running: its interaction, its task and what it has said."""

    def __init__(
        self,
        *,
        id_: str,
        host: str,
        runner: str,
        interaction: Interaction,
        attempt: LoginAttempt,
    ) -> None:
        self.id = id_
        self.host = host
        self.runner = runner
        self.interaction = interaction
        self.attempt = attempt
        self.said = attempt.prompt
        self.task: asyncio.Task[None] | None = None

    def shown(self) -> LoginAttempt:
        """The attempt, as the API shows it (the latest thing the harness said included)."""
        return self.attempt.model_copy(update={"prompt": self.said})


_ASK_FAILED = (PluginError, NotAllowed, DoesNotExist, ConfigError, ToolUnavailable, TimeoutError)
"""What asking a harness can fail with: a runner udl cannot make, one that cannot answer, and one
that runs past the time it is given (a shell's `wait_for`, which is a `TimeoutError`). Each
becomes a problem in its own row, never the end of the listing."""


def _why(failure: Exception) -> str:
    """What to say about something that could not be asked: a shell's timeout carries no message
    of its own, so it is named with the time it was given (and an empty message says so)."""
    if isinstance(failure, TimeoutError):
        return f"it did not answer in {DEFAULT_TIMEOUT.total_seconds():.0f}s"
    return str(failure) or "it failed without saying why"


class Harnesses:
    """The harnesses of every host: what each one is, and how to install, set up and sign it in."""

    def __init__(self, service: UDLService) -> None:
        self._service = service
        self._attempts: dict[str, _Attempt] = {}
        self._limited: dict[tuple[str, str], AwareDatetime] = {}
        """When each (host, runner)'s quota is back, as the dispatcher last saw it (27.7)."""
        self._sessions: dict[tuple[str, str], set[str]] = {}
        """The sessions running on each (host, runner): an update waits for them to end."""

    # -- reading -------------------------------------------------------------------------------

    async def all(self, request: ListRunnersRequest) -> RunnerStatusList:
        """Every harness on every host (or one host's), with what each says about itself."""
        hosts = (
            [await self._service.hosts.get(request.host)]
            if request.host
            else (await self._service.hosts.all())
        )
        items: list[RunnerStatus] = []
        for host in hosts:
            for name in self._names():
                health, models = await self._checked(host.name, name)
                items.append(
                    RunnerStatus(host=host.name, runner=name, health=health, models=models)
                )
        # (Every harness has its row, whatever went wrong: what could not be asked is in its
        # health's problems, where the listing shows it, not dropped from the listing.)
        return RunnerStatusList(items=items, problems=[])

    async def _checked(self, host: str, name: str) -> tuple[RunnerHealth, list[RunnerModel]]:
        """What the harness called `name` on `host` says about itself, and its models. Anything
        that cannot be asked is a problem in its health, never an exception and never a missing
        row: a harness udl cannot make (its settings do not fit it), one whose doctor fails, and
        one whose models cannot be listed all still say which harness they are."""
        try:
            runner = await self.runner(host, name)
        except _ASK_FAILED as failure:
            return (
                RunnerHealth(installed=False, problems=[f"udl cannot make it: {_why(failure)}"]),
                [],
            )
        try:
            health = await self._health(host, name, runner)
        except _ASK_FAILED as failure:
            health = RunnerHealth(
                installed=False, problems=[f"udl could not ask it: {_why(failure)}"]
            )
        models: list[RunnerModel] = []
        try:
            models = await runner.models()
        except _ASK_FAILED as failure:
            health = health.model_copy(
                update={"problems": [*health.problems, f"its models: {_why(failure)}"]}
            )
        return health, models

    def _names(self) -> list[str]:
        """The runners udl has, sorted."""
        return [info.name for info in self._service.registry.plugins(RUNNERS)]

    async def runner(self, host: str, name: str) -> AnAgentRunner:
        """The runner called `name`, on the host called `host`, made from its settings. Raises
        DoesNotExist for a host or runner there is not, ConfigError for settings that do not fit
        it."""
        cls = self._service.registry.get(RUNNERS, name)
        try:
            made = await self._service.plugin_settings(name)
        except DoesNotExist:
            # (A runner's options are its config file table until it declares a settings model.)
            try:
                made = cls.options_model.model_validate(
                    self._service.settings.integrations.get(name, {})
                )
            except ValidationError as failure:
                raise ConfigError(
                    f"The {name} runner's settings do not fit it: {failure}"
                ) from failure
        if not isinstance(made, cls.options_model):
            raise ConfigError(
                f"The {name} runner's settings are {type(made).__qualname__}, not its "
                f"options_model's ({cls.options_model.__qualname__})"
            )
        return cls(made, shell=await self._service.hosts.shell(host), clock=self._service.clock)

    async def _health(self, host: str, name: str, runner: AnAgentRunner) -> RunnerHealth:
        """The runner's health, with what the dispatcher last saw of its quota (27.7)."""
        health = await runner.health()
        return health.model_copy(update={"limited_until": self._limited.get((host, name))})

    def limited(self, host: str, runner: str, until: AwareDatetime | None) -> None:
        """Note that the harness on `host` is out of quota until `until` (what the dispatcher
        sees, 27.7); None forgets it."""
        if until is None:
            self._limited.pop((host, runner), None)
        else:
            self._limited[(host, runner)] = until

    def started(self, host: str, runner: str, session: str) -> None:
        """Note that a session is running there (an update waits for it to end)."""
        self._sessions.setdefault((host, runner), set()).add(session)

    def ended(self, host: str, runner: str, session: str) -> None:
        """Note that a session has ended."""
        running = self._sessions.get((host, runner))
        if running is not None:
            running.discard(session)

    def running(self, host: str, runner: str) -> int:
        """How many sessions are running there."""
        return len(self._sessions.get((host, runner), ()))

    # -- installing and setting up --------------------------------------------------------------

    async def install(self, request: InstallRunnerRequest) -> RunnerInstalled:
        """Install or update a harness on a host: only its own fixed argv, and never while a
        session is running there. Raises NotAllowed for a version that is not one, or while a
        session runs."""
        if request.version is not None and not VERSION.fullmatch(request.version):
            raise NotAllowed(
                f"{request.version!r} is not a version udl will install",
                hint="A version is letters, digits, dots and dashes (`latest`, `2.1.4`).",
            )
        if self.running(request.host, request.runner):
            raise NotAllowed(
                f"{request.runner} has a session running on {request.host}",
                hint="An update waits for it to end (an upgraded harness mid-turn is lost work).",
            )
        runner = await self.runner(request.host, request.runner)
        output: list[str] = []
        async for line in runner.install(request.version):
            output.append(line)
        health = await self._health(request.host, request.runner, runner)
        return RunnerInstalled(
            host=request.host,
            runner=request.runner,
            version=health.version,
            output="".join(output)[-TAIL:],
        )

    async def setup(
        self,
        request: SetupRunnerRequest,
        *,
        roles: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> RunnerSetup:
        """Write a harness's deny policies and one agent file per role on a host, and set the
        service's environment (`env`: a provider's key by variable name). Idempotent."""
        runner = await self.runner(request.host, request.runner)
        files = await runner.setup(roles, env)
        health = await self._health(request.host, request.runner, runner)
        return RunnerSetup(
            host=request.host,
            runner=request.runner,
            guards=health.guards,
            files=files,
        )

    # -- signing in and out --------------------------------------------------------------------

    async def login(self, request: StartLoginRequest) -> LoginAttempt:
        """Sign a harness in, and relay what it asks: one attempt per (host, runner) at a time.
        Raises NotAllowed while another attempt is running."""
        self._forget_finished()
        for attempt in self._attempts.values():
            if (attempt.host, attempt.runner) == (request.host, request.runner):
                raise NotAllowed(
                    f"{request.runner} is already signing in on {request.host}",
                    hint=f"Its attempt is {attempt.id}: answer it, or wait for it to end.",
                )
        runner = await self.runner(request.host, request.runner)
        try:
            interaction = await runner.login(request.provider)
        except LoginByHand as by_hand:
            # (A harness that cannot be driven without a terminal: the command, not a failure.
            # Any other error is udl's own, and goes to the caller as it is.)
            attempt = LoginAttempt(
                id=str(uuid4()),
                host=request.host,
                runner=request.runner,
                done=True,
                ok=False,
                prompt="\n".join(part for part in (by_hand.message, by_hand.hint) if part),
                command=by_hand.command,
            )
            self._attempts[attempt.id] = _Attempt(
                id_=attempt.id,
                host=request.host,
                runner=request.runner,
                interaction=_Ended(),
                attempt=attempt,
            )
            return attempt
        attempt = LoginAttempt(id=str(uuid4()), host=request.host, runner=request.runner)
        running = _Attempt(
            id_=attempt.id,
            host=request.host,
            runner=request.runner,
            interaction=interaction,
            attempt=attempt,
        )
        self._attempts[attempt.id] = running
        running.task = asyncio.create_task(
            self._watch(running), name=f"udl login {request.host}/{request.runner}"
        )
        return await self._first(running)

    async def reply(self, request: LoginReplyRequest) -> LoginAttempt:
        """Give a running sign-in what it asked for (a code, or a line)."""
        running = self._attempt(request.id)
        await running.interaction.write(request.text)
        return await self._read(running)

    async def status(self, request: LoginStatusRequest) -> LoginStatus:
        """How a sign-in is going (and whether it ended signed in)."""
        running = self._attempt(request.id)
        if running.attempt.done:
            return LoginStatus(attempt=running.shown())
        return LoginStatus(attempt=await self._read(running))

    async def logout(self, request: LogoutRunnerRequest) -> RunnerSignedOut:
        """Sign a harness out. `confirm` must be `<host>/<runner>`, so it is never a typo."""
        if request.confirm != f"{request.host}/{request.runner}":
            raise NotAllowed(
                f"confirm says {request.confirm!r}, not {request.host}/{request.runner}",
                hint="A sign-out names the host and runner it signs out.",
            )
        runner = await self.runner(request.host, request.runner)
        await runner.logout()
        return RunnerSignedOut(host=request.host, runner=request.runner)

    def _attempt(self, id_: str) -> _Attempt:
        found = self._attempts.get(id_)
        if found is None:
            raise DoesNotExist(
                f"No sign-in attempt {id_!r}",
                hint="An attempt ends after ten minutes, and is forgotten when the next starts.",
            )
        return found

    def _forget_finished(self) -> None:
        """Drop the attempts that have ended (they are only kept until the next one starts)."""
        for id_, running in list(self._attempts.items()):
            if running.attempt.done and (running.task is None or running.task.done()):
                del self._attempts[id_]

    async def _first(self, running: _Attempt) -> LoginAttempt:
        """What a harness printed as its attempt started (it has a moment to say it)."""
        said = await running.interaction.read()
        if said:
            running.said = (running.said + said).strip()
        running.attempt = running.attempt.model_copy(
            update={"prompt": running.said, "expects": _expects(running.said)}
        )
        return running.shown()

    async def _read(self, running: _Attempt) -> LoginAttempt:
        """What a harness has printed since the last read."""
        if not running.attempt.done:
            said = await running.interaction.read()
            if said:
                running.said = (running.said + said).strip()
                running.attempt = running.attempt.model_copy(
                    update={"prompt": running.said, "expects": _expects(running.said)}
                )
        return running.shown()

    async def _watch(self, running: _Attempt) -> None:
        """Wait for the sign-in to end (or ten minutes), then say how it ended and let it go."""
        ok = False
        try:
            async with self._service.clock.timeout(LOGIN_TIMEOUT):
                code = await running.interaction.wait()
            ok = code == 0
        except TimeoutError:
            running.said = f"{running.said}\n(udl ended it after ten minutes)".strip()
        finally:
            await running.interaction.close()
            running.attempt = running.attempt.model_copy(
                update={"prompt": running.said, "done": True, "ok": ok}
            )

    async def stop(self) -> None:
        """End every sign-in that is running (the service is going down)."""
        for running in self._attempts.values():
            if running.task is not None and not running.task.done():
                running.task.cancel()
        await asyncio.gather(
            *(running.task for running in self._attempts.values() if running.task is not None),
            return_exceptions=True,
        )


class _Ended:
    """An interaction that has already ended (a harness udl cannot drive)."""

    running = False

    async def read(self) -> str:
        return ""

    async def write(self, text: str) -> None:
        return

    async def close(self) -> None:
        return

    async def wait(self) -> int:
        return 1


def _expects(said: str) -> Literal["none", "code"]:
    """What a sign-in wants back: a code, when it asks for one (`paste the code`, `enter code`).
    A harness's own name is not an ask: "opencode" and "Claude Code" are matched as the words they
    are, so neither makes every sign-in look like it takes a code."""
    return "code" if _ASKS.search(said) else "none"


_ASKS: Final = re.compile(
    r"\b(?:paste|enter|type|provide|send)\b[^\n]{0,40}?\bcode\b"
    r"|\b(?:the|your|this|that)\s+code\b"
    r"|\bcode\s*[:?]",
    re.IGNORECASE,
)
"""What a harness says when it is asking for a code (and nothing it says otherwise)."""
