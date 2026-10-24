"""
----------------
- operation.py -
----------------

The operation action: calls one of udl's operations (or a plugin's, such as gmail_send_message)
with arguments rendered from the event, e.g. downloading the links in an email:

    {"operation": "download", "arguments": {"urls": "{{ event.data.body_text | urls }}"}}

The arguments are validated by the operation's request model when the action is saved (with an
event type's example) and again when it runs. An admin operation (one that changes automations,
accounts, AI setup or settings) is never one an action calls, so no event can change udl's
setup. It runs as the automations do (current_caller), so the paths it gives are held to their
roots, as an API client's are.
"""

# System Imports
import logging
from typing import Any, Final

# Package Imports
from pydantic import Field, ValidationError

# Local Imports
from udl.automations.events import EventType
from udl.core.operations import Caller, Operation, current_caller
from udl.db.models import JsonObject
from udl.errors import (
    ActionFailed,
    Cancelled,
    ConfigError,
    DoesNotExist,
    NotAllowed,
    RateLimited,
    UDLError,
    is_retryable,
)
from udl.plugins.actions.base import (
    ActionContext,
    ActionOptions,
    ActionOutput,
    ActionPlugin,
    has_template,
    untemplated_problems,
)
from udl.process import cancellable

log = logging.getLogger(__name__)

_CANCELLED: Final = "The run was cancelled"


class OperationOptions(ActionOptions):
    operation: str = Field(min_length=1)
    """The operation to call, by name (as its MCP tool is called: e.g. download); never an admin
    one."""
    arguments: JsonObject = Field(default_factory=dict)
    """Its request: the operation's fields (texts in them are templates, rendered with the
    event)."""


class OperationAction(ActionPlugin[OperationOptions]):
    """Calls an operation."""

    name = "operation"
    options_model = OperationOptions
    summary = (
        "Calls one of udl's operations (but an admin one) with arguments made from the event; "
        "its response is the run's output."
    )

    def operation(self, name: str) -> Operation:
        """The operation called `name`, which an action may call. Raises ConfigError, or
        NotAllowed for an admin operation."""
        if has_template(name):
            raise ConfigError(
                f"An operation action names its operation plainly, not as a template ({name!r})"
            )
        try:
            op = self.service.operations.get(name)
        except DoesNotExist as e:
            raise ConfigError(e.message, hint=e.hint) from e
        if op.hints.admin:
            raise NotAllowed(
                f"{name} is an admin operation (it changes automations, accounts or settings): "
                "an action never calls one",
                hint="Call it yourself (through the CLI, the API or MCP).",
            )
        return op

    async def check(self, options: JsonObject, event_type: EventType[Any] | None) -> None:
        found = self.checked(options, event_type)
        op = self.operation(str(options["operation"]))
        if found is not None:
            try:
                op.request.model_validate(found.arguments)
            except ValidationError as e:
                raise ConfigError(f"{op.name}'s arguments are not valid: {_problems(e)}") from e
            return
        arguments = options.get("arguments", {})
        if isinstance(arguments, dict):
            problems = untemplated_problems(op.request, arguments)
            if problems is not None:
                raise ConfigError(f"{op.name}'s arguments are not valid: {problems}")

    async def run(self, ctx: ActionContext[OperationOptions]) -> ActionOutput:
        try:
            op = self.operation(ctx.options.operation)
        except UDLError as e:
            raise ActionFailed(e.message, hint=e.hint) from e
        try:
            request = op.request.model_validate(ctx.options.arguments)
        except ValidationError as e:
            raise ActionFailed(f"{op.name}'s arguments are not valid: {_problems(e)}") from e
        called = current_caller.set(Caller.AUTOMATION)  # (the task below runs as it)
        try:
            response = await cancellable(
                self.service.call(op.name, request), ctx.cancel, message=_CANCELLED
            )
        except Cancelled:
            raise
        except UDLError as e:
            retry_after = e.retry_after if isinstance(e, RateLimited) else None
            raise ActionFailed(
                e.message, retryable=is_retryable(e), retry_after=retry_after, hint=e.hint
            ) from e
        except Exception as e:
            log.exception("The %s operation failed unexpectedly", op.name)
            raise ActionFailed(f"{op.name} failed: {type(e).__name__}: {e}") from e
        finally:
            current_caller.reset(called)
        return ActionOutput(output=response.model_dump(mode="json"), summary=op.title)


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, problem['loc'])) or 'arguments'}: {problem['msg']}"
        for problem in error.errors()
    )
