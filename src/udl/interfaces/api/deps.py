"""
-----------
- deps.py -
-----------

What the routes share: the app's service, and the docs each route takes from its operation
"""

# System Imports
from typing import Annotated, Any

# Package Imports
from fastapi import Depends, Request

# Local Imports
from udl.core.operations import Operation, operations
from udl.core.service import UDLService
from udl.errors import ErrorInfo


def service(request: Request) -> UDLService:
    """The app's service (its lifespan starts and closes it)."""
    return request.app.state.service


ServiceDep = Annotated[UDLService, Depends(service)]


def documented(name: str, *errors: int) -> dict[str, Any]:
    """The route arguments that make a route udl's operation called `name`: its operationId,
    summary and description, and the errors (ErrorInfo) it may answer with."""
    return documented_op(operations()[name], *errors)


def documented_op(op: Operation, *errors: int) -> dict[str, Any]:
    """The route arguments that make a route the operation `op` (see documented())."""
    return {
        "operation_id": op.name,
        "summary": op.summary,
        "description": op.description,
        "responses": {status: {"model": ErrorInfo} for status in errors},
    }
