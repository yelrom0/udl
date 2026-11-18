"""
---------------
- __init__.py -
---------------

Hosts: the machines agents run on, as one module. `udl.hosts` re-exports what the rest of udl
imports (the `Host` record, the `HostShell` seam, the shells, and the operations' models), so a
caller says `from udl.hosts import Host, shell_for`.

- `shell.py`: the `Host` record and the shell (LocalShell, SshShell) every remote action goes
  through. Nothing outside this package knows which transport a host uses (O-D11, O-C4).
- `models.py`: the hosts operations' requests and responses.
- `manager.py`: the operations themselves (Hosts, UDLService.hosts).
"""

# Local Imports
from udl.hosts.models import (
    HostCheck,
    HostInfo,
    HostList,
    HostRequest,
    HostsList,
    SetHostRequest,
    ToolStatus,
)
from udl.hosts.shell import (
    DEFAULT_TIMEOUT,
    KEPT,
    LOCAL,
    SSH_CONFIG,
    Command,
    Host,
    HostName,
    HostShell,
    HostTransport,
    Interaction,
    LocalShell,
    SshShell,
    remote_command,
    remote_stdin,
    shell_for,
    ssh_argv,
    ssh_forward_argv,
)

__all__ = [
    "DEFAULT_TIMEOUT",
    "KEPT",
    "LOCAL",
    "SSH_CONFIG",
    "Command",
    "Host",
    "HostCheck",
    "HostInfo",
    "HostList",
    "HostName",
    "HostRequest",
    "HostShell",
    "HostTransport",
    "HostsList",
    "Interaction",
    "LocalShell",
    "SetHostRequest",
    "SshShell",
    "ToolStatus",
    "remote_command",
    "remote_stdin",
    "shell_for",
    "ssh_argv",
    "ssh_forward_argv",
]
