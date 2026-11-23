"""
----------------
- test_hosts.py -
-----------------

Host records, and the quoting that makes a remote command safe: any argv and any folder, however
odd, must round-trip unchanged through a real sh (a property test), and a secret given as
secret_env must reach the program's environment without ever appearing in the command line
"""

# System Imports
import asyncio
import json
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

# Package Imports
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

# Local Imports
from udl.errors import PluginFailed, ToolUnavailable
from udl.hosts import (
    LOCAL,
    Command,
    Host,
    HostTransport,
    LocalShell,
    SshShell,
    remote_command,
    remote_stdin,
    shell_for,
    ssh_argv,
)

# A program that prints, as JSON, the argv it was given (after its own first argument, the
# environment names it should show), its folder and those environment values.
SHOW = (
    "import json,os,sys;"
    " names = sys.argv[1];"
    " print(json.dumps({'argv': sys.argv[2:], 'cwd': os.getcwd(),"
    " 'env': {k: os.environ.get(k) for k in json.loads(names)}}))"
)


def awkward_text() -> st.SearchStrategy[str]:
    """Text a shell would treat specially (quotes, expansions, separators, spaces).

    No NUL, surrogate or newline: a filename may not hold one, and the shell could not pass it.
    """
    return st.text(
        alphabet=st.sampled_from("ab09 _-'\"`;&|<>*?[]{}()\\!#~%^@=:+,/"),
        min_size=0,
        max_size=40,
    )


@st.composite
def folders(draw: st.DrawFn) -> str:
    """A folder that exists (under a fresh temporary root), its name awkward."""
    parts = draw(
        st.lists(
            awkward_text().filter(lambda part: part not in (".", "..") and "/" not in part),
            min_size=1,
            max_size=4,
        )
    )
    path = Path(tempfile.mkdtemp(prefix="udl-hosts-"))
    for part in parts:
        path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


@st.composite
def argvs(draw: st.DrawFn) -> tuple[str, ...]:
    """An argv of pieces that are not empty, so the program's name is never missing."""
    return tuple(draw(st.lists(awkward_text().filter(bool), min_size=1, max_size=6)))


def through_sh(
    command: Command, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run the remote command line the way ssh runs it: a login `sh -lc` on it, its stdin the
    command's (a login shell, so the program gets the PATH its user gets on the host)."""
    return subprocess.run(  # noqa: S603 - (a fixture's own command, and only sh's own quoting)
        ["sh", "-lc", remote_command(command)],  # noqa: S607 - (sh is always at /bin/sh's place)
        input=remote_stdin(command),
        capture_output=True,
        env=env,
        check=False,
    )


# ---------------------------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------------------------


def test_the_local_host_is_always_local() -> None:
    host = Host.local("/srv/udl/work")

    assert (host.name, host.transport) == (LOCAL, HostTransport.LOCAL)
    assert host.enabled is True
    assert host.env == {}


def test_another_host_is_reached_over_ssh_by_its_config_name() -> None:
    host = Host(name="thevault", transport=HostTransport.SSH, ssh="thevault", work_dir="/srv/udl")

    assert host.ssh == "thevault"


@pytest.mark.parametrize(
    ("fields", "problem"),
    [
        ({"name": "thevault", "transport": "local", "work_dir": "/w"}, "this machine"),
        ({"name": "local", "transport": "ssh", "ssh": "local", "work_dir": "/w"}, "this machine"),
        ({"name": "thevault", "transport": "ssh", "work_dir": "/w"}, "ssh"),
        ({"name": "local", "transport": "local", "work_dir": "/w"}, None),
    ],
)
def test_a_host_is_checked_against_its_transport(
    fields: dict[str, object], problem: str | None
) -> None:
    if problem is None:
        assert Host.model_validate(fields).name == "local"
        return
    with pytest.raises(ValidationError, match=problem):
        Host.model_validate(fields)


def test_a_host_gives_an_example_for_the_record_contract() -> None:
    assert Host.example().transport is HostTransport.SSH


def test_a_host_is_looked_up_by_its_name() -> None:
    assert "name" in Host.indexed
    assert Host.unique == (("name",),)


# ---------------------------------------------------------------------------------------------
# The quoting property: whatever goes in comes out the same
# ---------------------------------------------------------------------------------------------


@given(
    argv=argvs(),
    cwd=folders(),
    env=st.dictionaries(st.sampled_from(["A", "B", "LONG_NAME"]), awkward_text(), max_size=3),
)
@settings(max_examples=100, deadline=None)
def test_any_argv_and_folder_round_trip_through_the_remote_sh(
    argv: tuple[str, ...], cwd: str, env: dict[str, str]
) -> None:
    """The heart of SshShell: the command line it builds, run by a real sh, runs the program with
    the very argv, folder and environment it was given, whatever they contain."""
    run = through_sh(
        Command((sys.executable, "-c", SHOW, json.dumps(sorted(env)), *argv), cwd, env),
        env={"PATH": "/usr/bin:/bin", **env},
    )

    assert run.returncode == 0, run.stderr
    found = json.loads(run.stdout)
    assert found["argv"] == list(argv)
    assert found["cwd"] == cwd
    assert found["env"] == {name: env.get(name) for name in sorted(env)}


# ---------------------------------------------------------------------------------------------
# The remote command line itself
# ---------------------------------------------------------------------------------------------


def test_the_remote_command_changes_directory_and_exports_the_environment() -> None:
    script = remote_command(Command(("echo", "hi"), cwd="/srv/work dir", env={"B": "2", "A": "1"}))

    assert script == "cd -- '/srv/work dir' && { export A=1 && export B=2 && exec echo hi; }"


def test_a_secret_is_read_from_stdin_and_never_in_the_command_line() -> None:
    secret = "s3cret-Value; rm -rf /"  # noqa: S105 - (a test's)
    command = Command(("tool",), secret_env={"API_TOKEN": secret})

    script = remote_command(command)

    assert secret not in script
    assert "IFS= read -r API_TOKEN" in script
    assert "export API_TOKEN" in script  # (exported for the program, by name only)


def test_a_secret_reaches_the_programs_environment_not_its_argv() -> None:
    secret = "s3cret-Value; rm -rf /"  # noqa: S105 - (a test's)
    command = Command(
        (sys.executable, "-c", "import os; print(os.environ['TOKEN'])"),
        secret_env={"TOKEN": secret},
    )

    run = through_sh(command, env={"PATH": "/usr/bin:/bin"})

    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == secret.encode()
    assert secret not in " ".join(command.argv)


def test_the_stdin_has_the_secrets_first_a_line_each() -> None:
    command = Command(("tool",), secret_env={"A": "one", "B": "two"}, stdin=b"payload")

    assert remote_stdin(command) == b"one\ntwo\npayload"
    assert remote_stdin(Command(("tool",), stdin=b"payload")) == b"payload"


def test_a_secret_name_has_to_be_an_environment_name() -> None:
    with pytest.raises(ValueError, match="cannot name an environment variable"):
        Command(("tool",), secret_env={"not a name": "x"})


def test_a_name_cannot_be_both_environment_and_secret() -> None:
    with pytest.raises(ValueError, match="both env and secret_env"):
        Command(("tool",), env={"A": "1"}, secret_env={"A": "2"})


def test_an_environment_name_has_to_be_one() -> None:
    """An env name that is not a name would be spliced into the remote command line."""
    with pytest.raises(ValueError, match="cannot name an environment variable"):
        Command(("tool",), env={"BAD NAME": "1"})


def test_a_host_environment_name_has_to_be_one() -> None:
    with pytest.raises(ValueError, match="cannot name an environment variable"):
        Host(name="h", transport=HostTransport.SSH, ssh="h", work_dir="/w", env={"A B": "1"})


def test_a_secret_with_a_newline_is_refused() -> None:
    """A secret is framed on one line, so a newline would spill the rest into the input."""
    with pytest.raises(ValueError, match="has a newline"):
        Command(("tool",), secret_env={"TOKEN": "a\nb"})


def test_an_ssh_host_name_that_looks_like_an_option_is_refused() -> None:
    """`ssh -oProxyCommand=… <host>` would run a command on this machine."""
    with pytest.raises(ValueError, match="looks like an ssh option"):
        Host(
            name="h", transport=HostTransport.SSH, ssh="-oProxyCommand=touch /tmp/x", work_dir="/w"
        )


def test_a_failed_read_stops_the_command_before_the_program_runs() -> None:
    """The body is one `&&` chain: a read that gets nothing stops it, so the program never runs
    with an empty secret."""
    script = remote_command(Command(("tool",), secret_env={"TOKEN": "x"}))

    assert script == "IFS= read -r TOKEN && export TOKEN && exec tool"


def test_a_command_that_quotes_nothing_stays_readable() -> None:
    assert remote_command(Command(("uname", "-a"))) == "exec uname -a"


# ---------------------------------------------------------------------------------------------
# The shells
# ---------------------------------------------------------------------------------------------


def test_a_local_host_gets_a_local_shell() -> None:
    assert isinstance(shell_for(Host.local("/w")), LocalShell)


def test_an_ssh_host_gets_an_ssh_shell() -> None:
    host = Host(name="thevault", transport=HostTransport.SSH, ssh="thevault", work_dir="/w")

    assert isinstance(shell_for(host), SshShell)


def test_a_local_host_cannot_have_an_ssh_shell() -> None:
    with pytest.raises(ValueError, match="not reached over SSH"):
        SshShell(Host.local("/w"))


def test_an_ssh_host_cannot_have_a_local_shell() -> None:
    host = Host(name="thevault", transport=HostTransport.SSH, ssh="thevault", work_dir="/w")

    with pytest.raises(ValueError, match="not this machine"):
        LocalShell(host)


def test_the_ssh_argv_names_the_config_host_and_never_a_command_line() -> None:
    host = Host(name="thevault", transport=HostTransport.SSH, ssh="thevault", work_dir="/w")

    argv = ssh_argv(host, Command(("echo", "hi")))

    assert argv[0] == "ssh"
    assert "BatchMode=yes" in argv
    assert "ControlMaster=auto" in argv
    assert "thevault" in argv
    assert "--" in argv
    # (A login shell, so the program gets the PATH its user gets on that host.)
    assert argv[-1] == f"sh -lc {shlex.quote('exec echo hi')}"


def local() -> LocalShell:
    shell = shell_for(Host.local(str(tempfile.gettempdir())))
    assert isinstance(shell, LocalShell)
    return shell


# ---------------------------------------------------------------------------------------------
# LocalShell, against this machine
# ---------------------------------------------------------------------------------------------


async def test_a_local_shell_runs_a_program_and_gives_its_output() -> None:
    result = await local().run([sys.executable, "-c", "print('hello')"])

    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


async def test_a_local_shell_runs_it_in_the_folder_it_was_given(tmp_path: Path) -> None:
    result = await local().run(
        [sys.executable, "-c", "import os; print(os.getcwd())"], cwd=str(tmp_path)
    )

    assert result.stdout.strip() == str(tmp_path)


async def test_a_local_shell_gives_a_programs_environment() -> None:
    result = await local().run(
        [sys.executable, "-c", "import os; print(os.environ['SAID'])"], env={"SAID": "hi"}
    )

    assert result.stdout.strip() == "hi"


async def test_a_local_shell_keeps_a_secret_out_of_the_argv() -> None:
    secret = "s3cret-Value"  # noqa: S105 - (a test's)

    result = await local().run(
        [sys.executable, "-c", "import os; print(os.environ['TOKEN'])"],
        secret_env={"TOKEN": secret},
    )

    assert result.stdout.strip() == secret


async def test_a_local_shell_gives_the_program_its_input() -> None:
    result = await local().run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], stdin=b"hi"
    )

    assert result.stdout.strip() == "HI"


async def test_a_local_shell_fails_when_the_program_fails() -> None:
    with pytest.raises(PluginFailed, match="exit code 3"):
        await local().run([sys.executable, "-c", "import sys; sys.exit(3)"])


async def test_a_local_shell_gives_a_missing_program_as_tool_unavailable() -> None:
    with pytest.raises(ToolUnavailable, match="not installed"):
        await local().run(["udl-no-such-program-42"])


async def test_a_local_shell_is_timed_out() -> None:
    with pytest.raises(TimeoutError):
        await local().run(
            [sys.executable, "-c", "import time; time.sleep(5)"], within=timedelta(seconds=0.2)
        )


async def test_a_local_shell_streams_a_programs_output() -> None:
    chunks = [
        chunk
        async for chunk in local().stream(
            [sys.executable, "-c", "import sys; print('one'); print('two')"]
        )
    ]

    assert b"".join(chunks).decode().split() == ["one", "two"]


async def test_a_local_shell_interacts_with_a_program() -> None:
    talked = await local().interact([sys.executable, "-u", "-c", "print(input())"])

    assert talked.running
    await talked.write("hi")
    said = ""
    for _ in range(50):  # (the program prints once it has read the line)
        said += await talked.read()
        if "hi" in said:
            break
    assert said.strip() == "hi"
    await talked.close()  # (no more input, then it ends)
    assert await talked.wait() == 0
    assert not talked.running


async def test_a_local_shell_keeps_only_as_much_output_as_udl_keeps() -> None:
    """A program that prints more than udl keeps is read to the end, but only the first of its
    stdout (and the last of its stderr) is kept, and the result says so."""
    from udl.hosts import KEPT

    result = await local().run(
        [sys.executable, "-c", f"import sys; sys.stdout.write('x' * ({KEPT} + 5000))"]
    )

    assert result.returncode == 0
    assert len(result.stdout) == KEPT
    assert result.truncated is True


async def test_a_stream_is_stopped_and_killed_when_a_program_ignores_the_ask() -> None:
    """A program that ignores the ask to end is killed, so a stream never hangs."""
    shell = local()
    stubborn = (
        "import signal, time;"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        " print('started', flush=True);"
        " time.sleep(30)"
    )

    streamed = shell.stream([sys.executable, "-c", stubborn])
    first = await anext(streamed)
    assert first.strip() == b"started"
    await streamed.aclose()  # (asks it to end; it ignores that, so it is killed)

    # (The stream ended, so the process is gone: a later run works.)
    assert (await shell.run([sys.executable, "-c", "print('after')"])).stdout.strip() == "after"


async def test_an_interaction_reads_nothing_when_nothing_is_printed() -> None:
    """read() gives "" rather than waiting forever, so a caller is never stuck."""
    talked = await local().interact([sys.executable, "-c", "import time; time.sleep(0.2)"])

    assert await talked.read() == ""
    await talked.close()


async def test_a_read_that_follows_buffered_text_waits_again() -> None:
    """A read that drains text it already had must not leave the next one returning "" at once."""
    # (Each line in one write: under `-u`, print() writes its text and its newline separately, and
    # a reader that wakes between the two gets "one", then "\n" on its own. On a busy machine it
    # does: CI's four workers on four cores did, and so does pinning this test to one core.)
    talked = await local().interact(
        [
            sys.executable,
            "-c",
            "import os, time; os.write(1, b'one\\n'); time.sleep(0.3); os.write(1, b'two\\n')",
        ]
    )

    assert (await talked.read()).strip() == "one"
    # (It waits for the second line rather than giving "" straight away, because the wake-up was
    # already set by the first.)
    assert (await talked.read()).strip() == "two"
    await talked.close()


async def test_a_read_after_the_program_ended_returns_nothing_at_once() -> None:
    """Once the program has ended there is nothing more to wait for, so a read gives "" straight
    away rather than waiting out the call's time."""
    talked = await local().interact([sys.executable, "-u", "-c", "print('bye')"])

    assert await talked.wait() == 0  # (it has ended before anything is read)
    await asyncio.sleep(0.2)  # (let the reader reach its end-of-output)
    assert (await talked.read()).strip() == "bye"
    started = time.monotonic()
    assert await talked.read() == ""  # (nothing more: at once, not after the call's time)
    assert time.monotonic() - started < 5
    await talked.close()


async def test_a_local_shell_forwards_a_port_to_itself() -> None:
    async with local().forward(8765) as url:
        assert url == "http://127.0.0.1:8765"
