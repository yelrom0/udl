"""
-----------------------
- test_host_shell.py -
-----------------------

The same HostShell contract, for local and for ssh: a program runs in the folder it was given, a
secret reaches its environment but never its argv, its input reaches it, a long program is
stopped, and the shell never knows which transport it is.

The ssh side runs against a test SSH server on localhost (tests/support/ssh_server.py, asyncssh,
a dev dependency only): never a real host. It is skipped (not failed) when ssh is not installed
or the server cannot start.
"""

# System Imports
import asyncio
import gc
import os
import shutil
import sys
import threading
import urllib.request
from collections.abc import AsyncIterator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# Package Imports
import pytest

# Local Imports
from support.ssh_server import sshd
from udl.errors import Cancelled, PluginFailed, ToolUnavailable
from udl.hosts import (
    SSH_CONFIG,
    Host,
    HostShell,
    HostTransport,
    shell_for,
    ssh_forward_argv,
)
from udl.hosts.manager import Hosts
from udl.process import CancelToken

pytestmark = pytest.mark.integration

SECRET = "s3cret-Value_42"  # noqa: S105 - (a test's)


@pytest.fixture
def home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home of the test's own, with ssh's config and keys in it: ssh reads nothing of the
    real user's (tests/conftest.py already points XDG_CONFIG_HOME there; ~/.ssh is read from
    HOME, so point that too)."""
    root = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(root))
    return root


def _ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ssh at the real system ssh.

    The suite runs with a PATH of the venv's own (so a tool's absence is never a surprise), and
    SshShell runs `ssh` by name: put the system's directories back for the test, as the
    downloader tests do for their fake tools.
    """
    monkeypatch.setenv("PATH", f"{os.defpath}:/usr/local/bin:{os.environ.get('PATH', '')}")


@pytest.fixture
async def both(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[list[tuple[str, HostShell]]]:
    """Every shell the contract runs on: (name, shell)."""
    _ssh(monkeypatch)
    if shutil.which("ssh") is None:
        pytest.skip("ssh is not installed")
    made: list[tuple[str, HostShell]] = [("local", shell_for(Host.local(str(home))))]
    async with sshd(home) as name:
        # (ssh reads ~/.ssh/config from the passwd entry, not from $HOME, so the test points it
        # at the config its fixture wrote.)
        monkeypatch.setenv(SSH_CONFIG, str(home / ".ssh" / "config"))
        host = Host(name=name, transport=HostTransport.SSH, ssh=name, work_dir=str(home))
        made.append(("ssh", shell_for(host)))
        yield made


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


async def test_both_shells_run_a_program_and_give_its_output(
    both: list[tuple[str, HostShell]],
) -> None:
    for name, shell in both:
        result = await shell.run(_python("print('hello')"))

        assert result.returncode == 0, name
        assert result.stdout.strip() == "hello", name


async def test_both_shells_run_a_program_in_the_folder_it_was_given(
    both: list[tuple[str, HostShell]], tmp_path: Path
) -> None:
    for name, shell in both:
        result = await shell.run(_python("import os; print(os.getcwd())"), cwd=str(tmp_path))

        assert result.stdout.strip() == str(tmp_path), name


async def test_both_shells_give_a_programs_environment(both: list[tuple[str, HostShell]]) -> None:
    for name, shell in both:
        result = await shell.run(
            _python("import os; print(os.environ['SAID'])"), env={"SAID": "hi"}
        )

        assert result.stdout.strip() == "hi", name


async def test_both_shells_give_a_secret_to_the_environment_but_not_the_argv(
    both: list[tuple[str, HostShell]],
) -> None:
    for name, shell in both:
        result = await shell.run(
            _python("import os; print(os.environ['TOKEN'])"), secret_env={"TOKEN": SECRET}
        )

        assert result.stdout.strip() == SECRET, name


async def test_a_secret_and_stdin_do_not_mix(both: list[tuple[str, HostShell]]) -> None:
    """A secret is in the environment, not in the program's input: a program that reads stdin
    reads only what the caller gave it."""
    for name, shell in both:
        result = await shell.run(
            _python("import os, sys; print(sys.stdin.read().upper(), os.environ['TOKEN'])"),
            secret_env={"TOKEN": SECRET},
            stdin=b"payload",
        )

        assert result.stdout.split() == ["PAYLOAD", SECRET], name


async def test_a_secret_reaches_a_streaming_program(both: list[tuple[str, HostShell]]) -> None:
    """A program read through stream() gets its secret in its environment, so a harness that
    prints one while it works still has it."""
    for name, shell in both:
        chunks = [
            chunk
            async for chunk in shell.stream(
                _python("import os; print(os.environ['TOKEN'])"),
                secret_env={"TOKEN": SECRET},
            )
        ]

        assert b"".join(chunks).decode().strip() == SECRET, name


async def test_a_secret_reaches_a_program_that_is_talked_to(
    both: list[tuple[str, HostShell]],
) -> None:
    """A program read and written while it runs gets its secret too (25.11's sign-in)."""
    for name, shell in both:
        talked = await shell.interact(
            _python("import os; print('token=' + os.environ['TOKEN'])"),
            secret_env={"TOKEN": SECRET},
        )

        said = ""
        for _ in range(50):
            said += await talked.read()
            if "token=" in said:
                break
        assert f"token={SECRET}" in said, name
        await talked.close()


async def test_a_missing_folder_is_an_error_on_both_shells(
    both: list[tuple[str, HostShell]],
) -> None:
    """A folder that is not there is refused, not quietly replaced by the shell's own (which is
    what a remote `cd` failing would do)."""
    for _name, shell in both:
        with pytest.raises(PluginFailed):
            await shell.run(["/bin/pwd"], cwd="/no/such/folder/udl-test")


async def test_both_shells_give_a_programs_input(both: list[tuple[str, HostShell]]) -> None:
    for name, shell in both:
        result = await shell.run(
            _python("import sys; print(sys.stdin.read().upper())"), stdin=b"hi"
        )

        assert result.stdout.strip() == "HI", name


async def test_both_shells_fail_when_the_program_fails(both: list[tuple[str, HostShell]]) -> None:
    for _name, shell in both:
        with pytest.raises(PluginFailed, match="exit code 3"):
            await shell.run(_python("import sys; sys.exit(3)"))


async def test_both_shells_stop_a_program_that_runs_too_long(
    both: list[tuple[str, HostShell]],
) -> None:
    for _name, shell in both:
        with pytest.raises(TimeoutError):
            await shell.run(_python("import time; time.sleep(5)"), within=timedelta(seconds=0.5))


async def test_both_shells_cancel_a_running_program(both: list[tuple[str, HostShell]]) -> None:
    for _name, shell in both:
        cancel = CancelToken()

        async def run(shell: HostShell = shell, cancel: CancelToken = cancel) -> object:
            return await shell.run(
                _python("import time; time.sleep(5)"),
                cancel=cancel,
                within=timedelta(seconds=30),
            )

        task = asyncio.ensure_future(run())
        await asyncio.sleep(0.3)
        cancel.cancel()
        with pytest.raises(Cancelled, match="command was stopped"):
            await task


async def test_a_program_that_is_not_on_the_host_is_tool_unavailable(
    both: list[tuple[str, HostShell]],
) -> None:
    """ssh answers 127 for a command that is not there: udl says the program is not installed,
    not that it failed."""
    for _name, shell in both:
        with pytest.raises(ToolUnavailable, match="not installed"):
            await shell.run(["udl-no-such-program-42"])


async def test_an_unreachable_host_is_a_connection_failure(tmp_path: Path) -> None:
    """ssh's own 255 is not the program failing: it is udl not reaching the host."""
    host = Host(
        name="udl-no-such-host",
        transport=HostTransport.SSH,
        ssh="udl-no-such-host",
        work_dir=str(tmp_path),
    )
    with pytest.raises(PluginFailed, match="Could not reach the host"):
        await shell_for(host).run(["true"], within=timedelta(seconds=30))


async def test_a_tunnel_that_cannot_be_made_fails_instead_of_hanging(tmp_path: Path) -> None:
    """forward() waits for the tunnel to be ready, so a broken host is an error, not a URL that
    leads nowhere."""
    host = Host(
        name="udl-no-such-host",
        transport=HostTransport.SSH,
        ssh="udl-no-such-host",
        work_dir=str(tmp_path),
    )
    with pytest.raises(PluginFailed):
        async with shell_for(host).forward(9):
            pass  # pragma: no cover - (it must not get here)


async def test_a_tool_in_the_users_profile_is_found_over_ssh(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`udl hosts check` finds a harness its user installed: a host's shell is a login shell, so
    ~/.profile's PATH (which sshd's own default PATH lacks) is the one the program gets."""
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "claude").write_text("#!/bin/sh\necho claude-fake\n")
    (bin_dir / "claude").chmod(0o755)
    (home / ".profile").write_text('PATH="$HOME/.local/bin:$PATH"\nexport PATH\n')

    _ssh(monkeypatch)
    async with sshd(home) as name:
        monkeypatch.setenv(SSH_CONFIG, str(home / ".ssh" / "config"))
        shell = shell_for(
            Host(name=name, transport=HostTransport.SSH, ssh=name, work_dir=str(home))
        )
        statuses = {tool.name: tool for tool in await Hosts.tools(shell)}

    assert statuses["claude"].found is True
    assert statuses["claude"].version == "claude-fake"


async def test_both_shells_stream_a_programs_output(both: list[tuple[str, HostShell]]) -> None:
    for name, shell in both:
        chunks = [
            chunk async for chunk in shell.stream(_python("import sys; print('one'); print('two')"))
        ]

        assert b"".join(chunks).decode().split() == ["one", "two"], name


async def test_both_shells_interact_with_a_program(both: list[tuple[str, HostShell]]) -> None:
    for name, shell in both:
        talked = await shell.interact(_python("print(input())"))

        assert talked.running, name
        await talked.write("hi")
        said = ""
        for _ in range(50):  # (it prints once it has read the line)
            said += await talked.read()
            if "hi" in said:
                break
        assert said.strip() == "hi", name
        await talked.close()  # (no more input, then it is ended)
        assert await talked.wait() == 0, name


def _fetch(url: str) -> bytes:
    """Read `url` (a loopback one), on a worker thread: urllib blocks, so it is not called on
    the loop."""
    with urllib.request.urlopen(url, timeout=10) as answer:  # noqa: S310 - (a loopback URL)
        return bytes(answer.read())


async def test_a_tunnel_leaves_no_pipe_open(both: list[tuple[str, HostShell]]) -> None:
    """ssh's stderr goes to a file, not a pipe: a pipe left open is a transport asyncio warns
    about at the next collection (and a warning is an error here), which is what a ProxyCommand
    holding the pipe could always do."""
    for name, shell in both:
        if name == "local":
            continue
        async with shell.forward(8765):
            pass
        gc.collect()

    # (Nothing to assert: an unclosed transport raises at the collection above.)


async def test_both_shells_forward_a_loopback_port(
    both: list[tuple[str, HostShell]],
) -> None:
    """A port on the host is reachable on this machine through the shell's forward(): for local
    it is the same port; for ssh it is a tunnel (`ssh -N -L`) to a port on the far side.

    The local case is tried for real. The ssh case checks the tunnel's command: a real tunnel's
    data path needs a remote sshd that proxies direct-tcpip channels, which the test SSH server
    (asyncssh) does not, so it is exercised where it is used (25.9's OpenCode runner)."""

    class Hello(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(
            self,
            format: str,  # noqa: A002 - (the base class names the parameter `format`)
            *args: object,
        ) -> None:
            pass

    for name, shell in both:
        server = HTTPServer(("127.0.0.1", 0), Hello)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            async with shell.forward(port) as url:
                if name == "local":
                    # (For this machine the port is already on it: the URL is itself.)
                    assert url == f"http://127.0.0.1:{port}", name
                    body = await asyncio.to_thread(_fetch, url)
                    assert body == b"hello", name
                    continue
                # (For another host it is a tunnel to a loopback port on that host: the command
                # is `ssh -N -L 127.0.0.1:<local>:127.0.0.1:<port> <host>`.)
                local = int(urlsplit(url).port or 0)
                assert local, name
                argv = ssh_forward_argv(shell.host, port, local=local)
                assert argv[0] == "ssh", name
                assert "-N" in argv, name
                assert f"127.0.0.1:{local}:127.0.0.1:{port}" in argv, name
                assert argv[-1] == (shell.host.ssh or shell.host.name), name
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
