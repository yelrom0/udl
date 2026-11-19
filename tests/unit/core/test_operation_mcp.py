"""
-------------------------
- test_operation_mcp.py -
-------------------------

OperationHints.mcp: an operation that is no MCP tool (mcp=False, a merge or a power command) is
still a CLI command and an API route, and refuses an MCP or automation caller itself, so a
prompt-injected agent cannot reach it through another interface
"""

# System Imports
from typing import TYPE_CHECKING

# Package Imports
import pytest
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.core.operations import Caller, Operation, OperationHints, current_caller
from udl.errors import NotAllowed

if TYPE_CHECKING:
    from udl.core.service import UDLService


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class MergeRequest(_Model):
    """Merge a pull request."""

    number: int
    """Its number."""


class Merged(_Model):
    number: int
    """Its number."""


async def merge(service: UDLService, request: MergeRequest) -> Merged:
    """Merge a pull request, as a person asked."""
    return Merged(number=request.number)


def merge_operation() -> Operation:
    """An operation that is no MCP tool (it is destructive, and only a person merges)."""
    return Operation.of(
        merge,
        name="merge",
        title="Merge",
        hints=OperationHints(destructive=True, open_world=True, admin=True, mcp=False),
    )


def test_mcp_is_true_unless_said_otherwise() -> None:
    assert OperationHints().mcp is True
    assert OperationHints(mcp=False).mcp is False


@pytest.mark.parametrize("caller", [Caller.MCP, Caller.AUTOMATION])
async def test_an_operation_without_an_mcp_tool_refuses_an_agent(caller: Caller) -> None:
    operation = merge_operation()
    token = current_caller.set(caller)
    try:
        with pytest.raises(NotAllowed, match="not a tool for an AI agent"):
            await operation.method(None, MergeRequest(number=7))
    finally:
        current_caller.reset(token)


@pytest.mark.parametrize("caller", [None, Caller.CLI, Caller.API])
async def test_a_person_runs_it(caller: Caller | None) -> None:
    operation = merge_operation()
    token = current_caller.set(caller)
    try:
        assert await operation.method(None, MergeRequest(number=7)) == Merged(number=7)
    finally:
        current_caller.reset(token)
