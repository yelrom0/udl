"""
-------------
- models.py -
-------------

The host operations' requests and responses: which hosts there are, adding and changing one, and
a check's answer (the machine's `uname`, and whether each tool the runners need is installed).

The shell that acts on a host and the Host record itself are in udl.hosts.shell: nothing
above them knows which transport a host uses.
"""

# System Imports
from typing import Annotated, Self

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Local Imports
from udl.core.operations import ui_field
from udl.db.models import JsonObject
from udl.hosts.shell import Host, HostTransport

type HostRef = Annotated[str, StringConstraints(min_length=1, max_length=100)]
"""A host, by name: thevault, local."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class HostRequest(_Model):
    """A host, by name."""

    host: HostRef
    """The host: its name, as list_hosts gives it (e.g. thevault, local)."""


class SetHostRequest(_Model):
    """Add or change a host."""

    host: Host = Field(
        json_schema_extra=ui_field(
            label="Host",
            help="Its name, how udl reaches it (local or ssh), and where worktrees go on it.",
        )
    )
    """The host to add, or (to change one) the name of the host to change: its transport, ssh
    and work_dir say which it is."""
    update: JsonObject = Field(
        default_factory=dict,
        json_schema_extra=ui_field(
            label="Changes",
            widget="json",
            help="What to change on a host that is already there: work_dir, env, ssh or enabled.",
        ),
    )
    """What to change on the host that is there: work_dir, env, ssh or enabled."""


class HostInfo(_Model):
    """A host, as it is kept."""

    id: str
    """The host's id."""
    name: str
    """The host's name (e.g. thevault)."""
    transport: HostTransport
    """How udl reaches it: local or ssh."""
    ssh: str | None = None
    """The ~/.ssh/config host name, for an ssh host."""
    work_dir: str
    """Where worktrees go on it."""
    env: dict[str, str] = Field(default_factory=dict)
    """The environment programs get on it (not secrets)."""
    enabled: bool = True
    """Whether udl uses it."""

    @classmethod
    def of(cls, host: Host) -> Self:
        return cls(
            id=str(host.id),
            name=host.name,
            transport=host.transport,
            ssh=host.ssh,
            work_dir=host.work_dir,
            env=host.env,
            enabled=host.enabled,
        )


class ListHostsRequest(_Model):
    """List the hosts (local is always one)."""


class HostList(_Model):
    hosts: list[HostInfo]
    """The hosts, local first, then by name."""


class ToolStatus(_Model):
    """Whether a tool a runner needs is on a host."""

    name: str
    """The tool (e.g. git, claude)."""
    found: bool
    """Whether it is installed there."""
    version: str | None = None
    """What `--version` says, when it is installed and told us."""


class HostCheck(_Model):
    """What checking a host found: it answered, and what it has."""

    host: str
    """The host that was checked."""
    ok: bool
    """Whether udl could run a command there."""
    uname: str | None = None
    """What `uname -a` says there (the machine's system)."""
    tools: list[ToolStatus] = Field(default_factory=list[ToolStatus])
    """The tools the runners need, and whether each is installed there."""
    problem: str | None = None
    """Why it could not be checked, when it could not."""
