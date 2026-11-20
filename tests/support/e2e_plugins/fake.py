"""
----------------
- fake.py -
----------------

The e2e fake: an integration with no UI plugin of its own, so the browser tests see the page udl
generates for it (24.6) — its settings, an account connected with tokens, a read-only list, a
destructive operation with a `confirm_field`, and an event type.

It is a local plugin (`[plugin_dirs]` in the e2e config): udl imports this file and registers the
class. Nothing here reaches the network, so a test can run it anywhere.
"""

# System Imports
import asyncio
from collections.abc import Sequence
from typing import Any

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, SecretStr

# Local Imports
from udl.automations.events import EventType
from udl.core.operations import OperationHints, UISpec
from udl.errors import NotAllowed
from udl.plugins.integrations.base import (
    AccountIdentity,
    AccountOptions,
    ConnectMethod,
    Credentials,
    Integration,
    IntegrationSettings,
    ItemsPage,
    integration_operation,
)


class FakeSettings(IntegrationSettings):
    """The fake's settings: what a test changes from the generated form."""

    label: str = "a fake"
    """How the fake names itself."""
    api_key: SecretStr = SecretStr("fake-key")
    """A secret, so the form draws a password box that never shows it."""


class FakeAccountOptions(AccountOptions):
    """A fake account's options."""

    label: str = ""
    """What the account is called in the fake."""


class FakeTokens(BaseModel):
    """What a fake account is connected with."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr
    """The key the fake service accepts (anything but `refused`)."""


class Thing(BaseModel):
    """A thing the fake has."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    """Its id."""
    text: str
    """What it says."""


class ListThingsRequest(BaseModel):
    """What a list asks for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contains: str = ""
    """Only the things that say this."""


class DeleteThingRequest(BaseModel):
    """What deleting a thing asks for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    """The thing's id (the request's `name`: what a `confirm_field` has to repeat, 24.6)."""
    confirm: str = Field(default="")
    """The thing's id again: what makes the button enable (24.2's `confirm_field`)."""


class DeletedThing(BaseModel):
    """What deleting a thing answers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deleted: str
    """The id that was deleted."""


class ThingHappened(BaseModel):
    """What the fake's event says."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thing: str
    """The thing's id."""
    text: str
    """What it says."""


class FakeIntegration(Integration[FakeSettings, FakeAccountOptions]):
    """A fake service, for the browser tests."""

    name = "fake"
    title = "Fake"
    summary = "A fake service with no UI plugin of its own."
    settings_model = FakeSettings
    account_model = FakeAccountOptions
    connect = (ConnectMethod.TOKENS,)
    access_levels = ("read", "write")
    token_model = FakeTokens

    @classmethod
    def event_types(cls) -> Sequence[EventType[Any]]:
        return (
            EventType(
                name=f"{cls.name}.thing.happened",
                data=ThingHappened,
                summary="A thing happened in the fake.",
                example=ThingHappened(thing="t1", text="Hello"),
                subject=lambda data: f"Thing: {data.text}",
            ),
        )

    async def identify(
        self, credentials: Credentials, options: FakeAccountOptions
    ) -> AccountIdentity:
        if not isinstance(credentials, FakeTokens):
            raise NotAllowed("The fake connects accounts with tokens only")
        key = credentials.api_key.get_secret_value()
        if key == "refused":
            raise NotAllowed("The fake service refused the key")
        return AccountIdentity(id=f"key-{key}", display=f"Fake ...{key[-4:]}")

    async def watch(self, account: Any, events: Any, state: Any) -> None:
        # (It publishes nothing on its own: a test sees its event type listed, not fired.)
        await asyncio.sleep(3600)

    @integration_operation(
        "list_things",
        title="List the things",
        hints=OperationHints(read_only=True),
        access="read",
        fan_out=True,
        ui=UISpec(group="Reading", order=1),
    )
    async def list_things(self, account: Any, request: ListThingsRequest) -> ItemsPage[Thing]:
        """List the things in the account (a read-only list, which the page runs on open)."""
        await account.credentials()
        things = [
            Thing(id="t1", text="The first thing"),
            Thing(id="t2", text="The second thing"),
        ]
        return ItemsPage(items=[thing for thing in things if request.contains in thing.text])

    @integration_operation(
        "count_things",
        title="Count the things",
        hints=OperationHints(read_only=True),
        access="read",
        ui=UISpec(group="Reading", order=2),
    )
    async def count_things(self, account: Any, request: ListThingsRequest) -> Counted:
        """Count the things (24.7: a result the template plugin draws itself)."""
        await account.credentials()
        return Counted(count=2)

    @integration_operation(
        "ping",
        title="Ping",
        ui=UISpec(group="Writing", order=2, view="action"),
    )
    async def ping(self, account: Any, request: PingRequest) -> Pinged:
        """Say pong (24.7: an operation the template plugin hides)."""
        await account.credentials()
        return Pinged(pong=True)

    @integration_operation(
        "delete_thing",
        title="Delete a thing",
        hints=OperationHints(destructive=True),
        access="write",
        ui=UISpec(group="Writing", order=1, view="form", confirm_field="confirm"),
    )
    async def delete_thing(self, account: Any, request: DeleteThingRequest) -> DeletedThing:
        """Delete a thing (a destructive operation: the page asks first, and its `confirm` field
        must say the thing's name)."""
        await account.credentials()
        return DeletedThing(deleted=request.name)


class PingRequest(BaseModel):
    """Nothing to say (an action's own model, which has no fields)."""


class Counted(BaseModel):
    """How many things there are."""

    count: int


class Pinged(BaseModel):
    """That it answered."""

    pong: bool


# (What `udl.interfaces.adapters` looks for in a plugin's file: a module-level name bound to
# `Annotated[SomeModel, ...]` is a settings model; the class itself is the plugin.)
__all__ = ["FakeIntegration"]
