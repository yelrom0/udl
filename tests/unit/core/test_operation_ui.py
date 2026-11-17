"""
----------------------
- test_operation_ui.py -
----------------------

What an operation says about how the generated UI shows it: UISpec (accepted by Operation.of,
@operation and an integration's @integration_operation), the field hints ui_field() writes into a
request's JSON Schema, and what resolved_view means by `auto`
"""

# System Imports
from typing import TYPE_CHECKING, cast, get_args

# Package Imports
import pytest
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, ValidationError

# Local Imports
import udl.core.operations as ops
from support.engine import registry
from support.operations import lookup
from udl.core.operations import (
    Operation,
    OperationHints,
    OperationRegistry,
    UIModel,
    UISpec,
    Widget,
    check_ui_hints,
    operation,
    resolved_view,
    ui_field,
)
from udl.errors import PluginError
from udl.plugins.integrations.base import (
    AccountHandle,
    AccountIdentity,
    AccountOptions,
    ConnectMethod,
    Credentials,
    Integration,
    IntegrationSettings,
    integration_operation,
)
from udl.plugins.registry import INTEGRATIONS, SITES
from udl.plugins.sites.base import SitePlugin

if TYPE_CHECKING:
    from udl.core.service import UDLService as Service


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Empty(_Model):
    """Nothing."""


class Keyed(_Model):
    """Something, by key."""

    key: str
    """The key."""


class OptionalText(_Model):
    """Something optional."""

    text: str = "hi"
    """Some text."""


def make(
    name: str,
    request: type[BaseModel],
    *,
    ui: UISpec | None = None,
    read_only: bool = False,
    destructive: bool = False,
) -> Operation:
    """The operation called `name`, taking `request` and giving Empty."""

    async def run(service: Service, given: Empty) -> Empty:
        """Run it."""
        raise AssertionError

    run.__annotations__ = {"given": request, "return": Empty}
    return Operation.of(
        run,
        name=name,
        title="Run it",
        hints=OperationHints(read_only=read_only, destructive=destructive),
        ui=ui,
    )


# ---------------------------------------------------------------------------------------------
# resolved_view
# ---------------------------------------------------------------------------------------------


def test_auto_is_a_list_for_a_read_only_operation_asking_nothing_optional() -> None:
    assert resolved_view(make("plain", OptionalText, read_only=True)) == "list"


def test_auto_is_a_list_for_a_read_only_operation_that_takes_no_fields() -> None:
    # (24.6 runs a list view on open, so a read-only operation with nothing to ask for is a list,
    # even when its answer is a single thing.)
    assert resolved_view(make("plain", Empty, read_only=True)) == "list"


def test_auto_is_an_action_for_an_operation_that_changes_something_and_takes_no_fields() -> None:
    assert resolved_view(make("plain", Empty)) == "action"


def test_auto_is_a_detail_for_a_read_only_operation_that_needs_something() -> None:
    assert resolved_view(make("lookup", Keyed, read_only=True)) == "detail"


def test_auto_is_a_form_for_an_operation_that_changes_something_and_asks_something() -> None:
    assert resolved_view(make("keep", Keyed)) == "form"
    assert resolved_view(make("keep", OptionalText)) == "form"


def test_the_view_an_operation_says_it_has_is_the_one_it_has() -> None:
    stated = make("plain", Empty, ui=UISpec(view="detail"), read_only=True)

    assert resolved_view(stated) == "detail"
    # (auto means what it means for this operation.)
    assert resolved_view(make("plain", Empty, ui=UISpec(view="auto"), read_only=True)) == "list"


def test_every_operation_resolves_to_a_view_the_ui_knows() -> None:
    for op in OperationRegistry.core():
        assert resolved_view(op) in ("list", "detail", "form", "action")


# ---------------------------------------------------------------------------------------------
# UISpec
# ---------------------------------------------------------------------------------------------


def test_an_operation_has_no_ui_spec_unless_it_says_one() -> None:
    op = Operation.of(lookup, name="lookup", title="Look up")

    assert op.ui is None


def test_an_operation_takes_a_ui_spec() -> None:
    op = Operation.of(
        lookup,
        name="lookup",
        title="Look up",
        ui=UISpec(group="Things", order=-1, view="detail", icon="magnifying-glass"),
    )

    assert op.ui == UISpec(group="Things", order=-1, view="detail", icon="magnifying-glass")


def test_a_ui_spec_is_frozen_and_refuses_a_view_it_does_not_have() -> None:
    spec = UISpec()

    with pytest.raises(ValidationError):
        spec.group = "No"
    with pytest.raises(ValidationError, match="view"):
        UISpec(view="carousel")  # pyright: ignore[reportArgumentType]


def test_a_ui_spec_may_hide_an_operation_and_ask_for_a_confirmation_field() -> None:
    op = Operation.of(
        lookup,
        name="lookup",
        title="Look up",
        hints=OperationHints(destructive=True),
        ui=UISpec(hidden=True, confirm_field="key"),
    )

    assert op.ui is not None
    assert (op.ui.hidden, op.ui.confirm_field) == (True, "key")


def test_a_ui_spec_s_defaults_are_what_the_card_says() -> None:
    assert UISpec() == UISpec(
        group=None, order=0, view="auto", icon=None, hidden=False, confirm_field=None
    )


def test_a_confirmation_field_the_request_does_not_have_is_refused() -> None:
    async def drop(service: Service, given: Keyed) -> Empty:
        """Drop it."""
        raise AssertionError

    with pytest.raises(PluginError, match="nope"):
        Operation.of(
            drop,
            name="drop",
            title="Drop",
            hints=OperationHints(destructive=True),
            ui=UISpec(confirm_field="nope"),
        )

    # (One the request does have is fine.)
    assert (
        Operation.of(
            drop,
            name="drop",
            title="Drop",
            hints=OperationHints(destructive=True),
            ui=UISpec(confirm_field="key"),
        ).ui
        is not None
    )


def test_confirming_a_field_only_makes_sense_for_an_operation_that_changes_something() -> None:
    with pytest.raises(PluginError, match="destructive"):
        Operation.of(lookup, name="lookup", title="Look up", ui=UISpec(confirm_field="key"))


def test_an_operation_s_ui_spec_is_kept_when_it_is_made_one_of_a_plugin_s() -> None:
    op = Operation.of(
        lookup, name="lookup", title="Look up", ui=UISpec(group="Things")
    ).in_namespace("gizmo", kind=SITES.group)

    assert op.name == "gizmo_lookup"
    assert op.ui == UISpec(group="Things")


def test_a_plugin_s_operations_keep_the_ui_specs_they_say() -> None:
    class Showcase(SitePlugin):
        """A plugin with two operations that say how they are shown."""

        name = "showcase"

        @classmethod
        def operations(cls) -> list[Operation]:
            return [
                Operation.of(
                    lookup,
                    name="lookup",
                    title="Look up",
                    hints=OperationHints(read_only=True),
                    ui=UISpec(group="Things", order=2),
                ),
                Operation.of(
                    lookup,
                    name="hidden",
                    title="Hidden",
                    hints=OperationHints(read_only=True),
                    ui=UISpec(hidden=True),
                ),
            ]

    found = registry()
    found.register(SITES, Showcase)

    ops = OperationRegistry.discover(found)

    assert [op.name for op in ops.in_namespace("showcase")] == [
        "showcase_lookup",
        "showcase_hidden",
    ]
    assert ops.get("showcase_lookup").ui == UISpec(group="Things", order=2)
    assert ops.get("showcase_hidden").ui == UISpec(hidden=True)


def test_an_integration_s_operations_carry_the_ui_spec_they_say() -> None:
    class PingRequest(_Model):
        """Ask for a pong."""

        what: str
        """What to say."""

    class Pong(_Model):
        """A pong."""

        text: str
        """What was said."""

    class Ui(Integration[IntegrationSettings, AccountOptions]):
        """A fake integration whose operations say how they are shown (for tests)."""

        name = "uifake"
        title = "UI fake"
        summary = "A fake integration whose operations carry a UISpec (for tests)."
        connect = (ConnectMethod.NONE,)
        access_levels = ("read",)

        async def identify(
            self, credentials: Credentials, options: AccountOptions
        ) -> AccountIdentity:
            """Who a login is (nothing to ask a source that needs no login)."""
            return AccountIdentity(id="1", display="one")

        @integration_operation(
            "ping",
            title="Ping",
            hints=OperationHints(read_only=True),
            access="read",
            ui=UISpec(group="Pings", order=1, view="list"),
        )
        async def ping(self, account: AccountHandle[AccountOptions], request: PingRequest) -> Pong:
            """Say pong in an account."""
            return Pong(text=request.what)

        @integration_operation(
            "pong",
            title="Pong",
            hints=OperationHints(read_only=True),
            needs_account=False,
        )
        async def pong(self, request: PingRequest) -> Pong:
            """Say pong (no account needed), with nothing said about its view."""
            return Pong(text=request.what)

    found = registry()
    found.register(INTEGRATIONS, Ui)

    ops = OperationRegistry.discover(found)

    assert ops.get("uifake_ping").ui == UISpec(group="Pings", order=1, view="list")
    assert ops.get("uifake_ping").kind == INTEGRATIONS.group
    assert ops.get("uifake_pong").ui is None
    # (It reads and needs something, so it is a detail; nothing had to be said about its view.)
    assert resolved_view(ops.get("uifake_pong")) == "detail"


def registered() -> list[object]:
    """What @operation has registered (udl's own operations): the only way to see one."""
    return cast(list[object], ops._registered)  # pyright: ignore[reportPrivateUsage]


def test_an_operation_registered_with_a_ui_spec_carries_it() -> None:
    saved = list(registered())
    registered()[:] = []
    ops.operations.cache_clear()
    try:

        @operation(
            "probe_ui",
            title="Probe the UI",
            hints=OperationHints(read_only=True),
            ui=UISpec(group="Probe", icon="flask"),
        )
        async def probe(service: Service, given: Empty) -> Empty:
            """Probe the UI."""
            raise AssertionError

        [found] = ops.operations().values()

        assert found.name == "probe_ui"
        assert found.ui == UISpec(group="Probe", icon="flask")
        assert resolved_view(found) == "list"
    finally:
        registered()[:] = saved
        ops.operations.cache_clear()


# ---------------------------------------------------------------------------------------------
# ui_field
# ---------------------------------------------------------------------------------------------


def test_ui_field_writes_one_key_into_a_schema() -> None:
    class Form(UIModel):
        # (json_schema_extra is how a field's extra keys are given.)
        name: str = Field(json_schema_extra=ui_field(widget="text", label="Name", help="Its name"))
        about: str = Field(default="", json_schema_extra=ui_field(widget="markdown", advanced=True))

    schema = Form.model_json_schema()
    properties = schema["properties"]
    assert isinstance(properties, dict)

    assert properties["name"]["x-udl-ui"] == {
        "widget": "text",
        "label": "Name",
        "help": "Its name",
    }
    assert properties["about"]["x-udl-ui"] == {
        "widget": "markdown",
        "advanced": True,
    }


def test_ui_field_writes_only_what_it_was_given() -> None:
    assert ui_field() == {"x-udl-ui": {}}


def test_ui_field_orders_a_field_and_gives_it_a_placeholder() -> None:
    class Form(UIModel):
        b: str = Field(json_schema_extra=ui_field(order=2))
        a: str = Field(json_schema_extra=ui_field(order=1, placeholder="type here"))

    properties = Form.model_json_schema()["properties"]
    assert isinstance(properties, dict)

    assert properties["b"]["x-udl-ui"] == {"order": 2}
    assert properties["a"]["x-udl-ui"] == {
        "order": 1,
        "placeholder": "type here",
    }


def test_every_widget_a_field_may_name_is_allowed() -> None:
    # (The literal's own arguments, which is what WIDGETS is built from.)
    for named in get_args(Widget):
        assert ui_field(widget=named) == {"x-udl-ui": {"widget": named}}


def test_a_field_cannot_name_a_widget_udl_does_not_have() -> None:
    with pytest.raises(PluginError, match="carousel"):
        ui_field(widget="carousel")  # pyright: ignore[reportArgumentType]


def test_a_request_s_schema_keeps_what_ui_field_wrote() -> None:
    class Detailed(UIModel):
        """Something detailed."""

        key: str = Field(json_schema_extra=ui_field(label="Key", order=1))
        """The key."""
        tags: list[str] = Field(
            default_factory=list, json_schema_extra=ui_field(widget="multiselect")
        )
        """Some tags."""

    op = make("detailed", Detailed, read_only=True)

    properties = op.request.model_json_schema()["properties"]
    assert isinstance(properties, dict)

    assert properties["key"]["x-udl-ui"] == {"label": "Key", "order": 1}
    assert properties["tags"]["x-udl-ui"] == {"widget": "multiselect"}


# ---------------------------------------------------------------------------------------------
# The hints are checked
# ---------------------------------------------------------------------------------------------


def test_a_secret_is_never_shown_as_anything_but_a_password() -> None:
    class Login(UIModel):
        token: SecretStr = Field(json_schema_extra=ui_field(widget="text"))

    with pytest.raises(PluginError, match="token"):
        Login.model_json_schema()


def test_an_optional_secret_is_never_shown_as_anything_but_a_password() -> None:
    class Login(UIModel):
        token: SecretStr | None = Field(default=None, json_schema_extra=ui_field(widget="text"))

    with pytest.raises(PluginError, match="token"):
        Login.model_json_schema()


def test_a_password_widget_on_a_field_that_is_not_a_secret_is_fine() -> None:
    class Login(UIModel):
        token: str = Field(json_schema_extra=ui_field(widget="password"))

    properties = Login.model_json_schema()["properties"]
    assert isinstance(properties, dict)
    assert properties["token"]["x-udl-ui"] == {"widget": "password"}


def test_a_hand_written_unknown_widget_is_refused_too() -> None:
    # (A field need not use ui_field to say how it is shown.)
    class Form(UIModel):
        mode: str = Field(default="text", json_schema_extra={"x-udl-ui": {"widget": "carousel"}})

    with pytest.raises(PluginError, match="carousel"):
        Form.model_json_schema()
    with pytest.raises(PluginError, match="carousel"):
        check_ui_hints(Form)


def test_a_hint_that_is_not_a_dict_is_left_alone() -> None:
    class Odd(UIModel):
        # (json_schema_extra may be a callable, which udl does not read.)
        called: str = Field(default="", json_schema_extra=lambda schema: None)
        # (And a hint that is not a dict is not a hint.)
        spelled: str = Field(default="", json_schema_extra={"x-udl-ui": "text"})

    properties = Odd.model_json_schema()["properties"]
    assert isinstance(properties, dict)

    assert "called" in properties
    assert "spelled" in properties


def test_a_nested_model_s_hints_are_checked() -> None:
    class Inner(UIModel):
        token: SecretStr = Field(json_schema_extra=ui_field(widget="text"))

    class Outer(UIModel):
        inner: Inner | None = None
        """The inner model."""

    with pytest.raises(PluginError, match="token"):
        Outer.model_json_schema()


def test_a_nested_model_s_hints_reach_the_schema() -> None:
    class Inner(UIModel):
        key: str = Field(json_schema_extra=ui_field(label="Key"))

    class Outer(UIModel):
        inner: Inner | None = None
        """The inner model."""

    definitions = Outer.model_json_schema()["$defs"]
    assert isinstance(definitions, dict)

    assert definitions["Inner"]["properties"]["key"]["x-udl-ui"] == {"label": "Key"}


def test_json_schema_checks_and_returns_a_schema_as_json() -> None:
    class Form(UIModel):
        name: str = Field(json_schema_extra=ui_field(label="Name"))

    schema = ops.json_schema(Form)

    properties = cast(dict[str, JsonValue], schema["properties"])
    name = cast(dict[str, JsonValue], properties["name"])
    assert name["x-udl-ui"] == {"label": "Name"}


def test_json_schema_refuses_a_model_whose_hints_are_wrong() -> None:
    class Form(BaseModel):
        mode: str = Field(default="text", json_schema_extra={"x-udl-ui": {"widget": "carousel"}})

    with pytest.raises(PluginError, match="carousel"):
        ops.json_schema(Form)
