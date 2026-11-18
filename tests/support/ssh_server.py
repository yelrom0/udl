"""
-------------
- sshd.py -
-------------

A test SSH server on localhost: a real `ssh` (the same binary SshShell runs) reaches it through
a `~/.ssh/config` entry the fixture writes into the test's own home, with a key it makes for the
test. The server runs a session's command with `sh -c` and sends its output back, exactly as a
real host does — so a test proves udl's remote command line works over SSH, without talking to
any real host.
"""

# System Imports
import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

# Package Imports
import asyncssh

# The user the test server lets in, and the `~/.ssh/config` host name it is reached by.
USER = "udl-test"
NAME = "udl-test-host"


class _Server(asyncssh.SSHServer):
    """An SSH server that lets a key in and opens a session for each connection."""

    def begin_auth(self, username: str) -> bool:
        return True  # (a key is required: no password, no host-based)

    def session_requested(self) -> asyncssh.SSHServerSession[str]:
        """A session, with its command run by _serve()."""
        return asyncssh.SSHServerProcess(
            _factory, sftp_factory=None, sftp_version=0, allow_scp=False
        )


def _factory(process: asyncssh.SSHServerProcess[str]) -> None:
    """Start a session's command (asyncssh calls this when the session opens)."""
    asyncio.get_event_loop().create_task(_serve(process))


async def _serve(process: asyncssh.SSHServerProcess[str]) -> None:
    """Run the session's command through `sh -c` (as a host does), feeding it whatever the client
    sends on stdin and sending both its stdout and stderr back.

    Either side ending ends the other: the command's output closing ends the pump, and the client
    closing its stdin (or the channel) ends the command, so neither is left running."""
    shell = await asyncio.create_subprocess_shell(
        process.command or "",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    writer = process.stdout
    if shell.stdin is None or shell.stdout is None:  # pragma: no cover - (always piped)
        process.exit(1)
        return
    feeding_stdin = shell.stdin  # (pyright does not keep a check into a nested function)

    async def pump_in() -> None:
        """Feed the command whatever the client sends (a secret's lines, or a prompt)."""
        try:
            while chunk := await process.stdin.read(4096):
                feeding_stdin.write(chunk.encode())
                await feeding_stdin.drain()
        except OSError, asyncssh.Error:  # (the channel went away)
            pass
        finally:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
                feeding_stdin.close()

    feeding = asyncio.create_task(pump_in())
    try:
        while chunk := await shell.stdout.read(4096):
            writer.write(chunk.decode(errors="replace"))
            await writer.drain()
    except OSError, asyncssh.Error:  # (the channel went away)
        pass
    finally:
        feeding.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await feeding
        if shell.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                shell.terminate()
            try:
                await asyncio.wait_for(shell.wait(), timeout=5)
            except TimeoutError:  # (a slow machine, or a command that ignores the ask)
                with contextlib.suppress(ProcessLookupError):
                    shell.kill()
                await shell.wait()
        with contextlib.suppress(Exception):  # (the channel may already be gone)
            process.exit(shell.returncode or 0)


def _write_key(path: Path) -> None:
    """A fresh private key at `path` (0600: ssh refuses a key others can read).

    (asyncssh's generator is typed with a partial `**kwargs`, so this is the one place it is
    called, with its type pinned here.)"""
    private = asyncssh.generate_private_key("ssh-ed25519")  # pyright: ignore[reportUnknownMemberType] - (asyncssh's key helpers are untyped)
    private.write_private_key(str(path))  # pyright: ignore[reportUnknownMemberType] - (asyncssh's key helpers are untyped)
    path.chmod(0o600)


def _ssh_config(home: Path, port: int, key: Path) -> None:
    """The `~/.ssh/config` entry that reaches the test server (and nothing else)."""
    config = home / ".ssh" / "config"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"Host {NAME}\n"
        f"  HostName 127.0.0.1\n"
        "  StrictHostKeyChecking no\n"
        "  UserKnownHostsFile /dev/null\n"
        "  LogLevel ERROR\n"
        f"  Port {port}\n"
        f"  IdentityFile {key}\n"
        "  IdentitiesOnly yes\n"
        f"  User {USER}\n"
    )
    config.chmod(0o600)


@asynccontextmanager
async def sshd(home: Path) -> AsyncGenerator[str]:
    """A test SSH server on localhost, while the block runs: `home` is the test's own home (its
    `.ssh/config` gets the entry), and the block gets the host name to put in the Host's `ssh`
    field."""
    key_dir = home / ".ssh" / "udl-test"
    key_dir.mkdir(parents=True, exist_ok=True)
    host_key = key_dir / "host_ed25519"
    user_key = key_dir / "user_ed25519"
    for path in (host_key, user_key):
        _write_key(path)
    key = asyncssh.read_private_key(str(user_key))
    public = key.export_public_key()
    (key_dir / "user_ed25519.pub").write_text(f"{public.decode().strip()}\n")

    server = await asyncssh.create_server(
        _Server,
        "127.0.0.1",
        0,
        server_host_keys=[str(host_key)],
        authorized_client_keys=[str(key_dir / "user_ed25519.pub")],
    )
    port: int = server.sockets[0].getsockname()[1]
    _ssh_config(home, port, user_key)
    try:
        yield NAME
    finally:
        server.close()
        await server.wait_closed()
