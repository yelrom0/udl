"""
---------------------
- test_generated.py -
---------------------

The adapters udl makes for the operations plugins add: a CLI command (`udl <plugin> <op>`), an
API route (`POST /<kind>/<plugin>/<op>`) and an MCP tool (`<plugin>_<op>`), each made from the
operation's request model, so a plugin without a UI of its own is still fully usable. The same
request gives the same response through all three.
"""

# System Imports
import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

# Package Imports
import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from mcp import Client
from pydantic import BaseModel, ConfigDict, JsonValue

# Local Imports
from support.api import client_for as api_client_for
from support.cli import CLI
from support.engine import registry
from support.interfaces import Harness
from support.mcp import client_for as mcp_client_for
from support.operations import Colour, Echoed, EchoRequest, Gizmo, Point, echoed
from support.tasks import held
from udl.config import Settings
from udl.core.operations import Operation, operations
from udl.core.service import UDLService
from udl.errors import ErrorCode, ErrorInfo
from udl.interfaces.adapters import api_path, cli_groups
from udl.interfaces.api.app import create_app
from udl.interfaces.cli.common import operation_of
from udl.plugins.registry import SITES
from udl.plugins.sites.base import SitePlugin

ECHO = "/sites/gizmo/echo"


def gizmo_harness(tmp_path: Path) -> Harness:
    harness = Harness(tmp_path)
    harness.registry.register(SITES, Gizmo)
    return harness


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return gizmo_harness(tmp_path)


@pytest.fixture
def cli(harness: Harness) -> CLI:
    return CLI(harness)


def answer(text: str) -> Echoed:
    return Echoed.model_validate_json(text)


# ---------------------------------------------------------------------------------------------
# Parity: every operation a plugin adds is a command, a route and a tool
# ---------------------------------------------------------------------------------------------


def test_every_plugin_operation_has_its_cli_command(harness: Harness) -> None:
    groups = cli_groups(harness.service().operations)

    found = {
        f"{namespace} {name}": operation_of(inspect.unwrap(command.callback or print))
        for namespace, group in groups.items()
        for name, command in group.commands.items()
    }

    assert found == {"gizmo echo": "gizmo_echo", "gizmo lookup": "gizmo_lookup"}


def test_every_plugin_operation_has_its_api_route(harness: Harness) -> None:
    paths = create_app(harness.settings, make_service=harness.service).openapi()["paths"]

    assert paths[ECHO]["post"]["operationId"] == "gizmo_echo"
    assert paths["/sites/gizmo/lookup"]["post"]["operationId"] == "gizmo_lookup"


async def test_every_plugin_operation_has_its_mcp_tool(harness: Harness) -> None:
    async with mcp_client_for(harness) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    echo = tools["gizmo_echo"]
    assert echo.title == "Echo"
    assert echo.annotations is not None
    assert echo.annotations.read_only_hint is True
    assert set(echo.input_schema["properties"]) == set(EchoRequest.model_fields)
    assert echo.input_schema["required"] == ["text"]
    assert "gizmo_lookup" in tools


# ---------------------------------------------------------------------------------------------
# The CLI: an option per field
# ---------------------------------------------------------------------------------------------


def test_each_field_is_an_option(cli: CLI) -> None:
    result = cli(
        "gizmo",
        "echo",
        "--text",
        "hi",
        "--count",
        "3",
        "--flag",
        "--colour",
        "green",
        "--path",
        "a/b.txt",
        "--tags",
        "x",
        "--tags",
        "y",
        "--sizes",
        "1",
        "--sizes",
        "2",
        "--point",
        '{"x": 1, "y": 2}',
        "--extra",
        '[1, "two", null]',
        "--limit",
        "7",
        "--points",
        '{"x": 1}',
        "--points",
        '{"x": 2, "y": 3}',
        "--either",
        "5",
        "--json",
    )

    assert result.exit_code == 0, result.output
    assert answer(result.stdout) == echoed(
        EchoRequest(
            text="hi",
            count=3,
            flag=True,
            colour=Colour.GREEN,
            path=Path("a/b.txt"),
            tags=["x", "y"],
            sizes=[1, 2],
            point=Point(x=1, y=2),
            extra=[1, "two", None],
            limit=7,
            points=[Point(x=1), Point(x=2, y=3)],
            either=5,
        )
    )


def test_what_is_not_given_is_the_request_s_default(cli: CLI) -> None:
    result = cli("gizmo", "echo", "--text", "hi", "--json")

    assert result.exit_code == 0, result.output
    assert answer(result.stdout) == echoed(EchoRequest(text="hi"))


def test_a_flag_can_be_turned_off(cli: CLI) -> None:
    result = cli("gizmo", "echo", "--text", "hi", "--no-flag", "--json")

    assert answer(result.stdout).flag is False


def test_a_value_that_is_not_json_is_text(cli: CLI) -> None:
    result = cli(
        "gizmo", "echo", "--text", "hi", "--extra", "plain words", "--either", "five", "--json"
    )

    assert (answer(result.stdout).extra, answer(result.stdout).either) == ("plain words", "five")


def test_a_bad_config_is_said_rather_than_an_unknown_command(cli: CLI, tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("max_concurrent = 0\n")

    result = cli("--config", config, "gizmo", "echo", "--text", "hi")

    assert result.exit_code == 2
    assert "max_concurrent" in result.output


@pytest.mark.parametrize(
    ("args", "named"),
    [
        ((), "text"),  # (required)
        (("--text", "hi", "--count", "many"), "count"),
        (("--text", "hi", "--colour", "mauve"), "colour"),
        (("--text", "hi", "--limit", "11"), "limit"),
        (("--text", "hi", "--point", '{"y": 1}'), "point"),
        (("--text", "hi", "--point", "nowhere"), "point"),
    ],
)
def test_a_bad_request_is_a_usage_error_that_names_the_field(
    cli: CLI, args: tuple[str, ...], named: str
) -> None:
    result = cli("gizmo", "echo", *args)

    assert result.exit_code == 2
    assert named in result.output


def test_the_whole_request_can_come_from_a_file(cli: CLI, tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"text": "from the file", "count": 2, "tags": ["a"]}))

    result = cli("gizmo", "echo", "--input", request, "--count", "5", "--json")

    assert result.exit_code == 0, result.output
    # (An option beats the file.)
    assert answer(result.stdout) == echoed(EchoRequest(text="from the file", count=5, tags=["a"]))


def test_the_whole_request_can_come_from_stdin_as_yaml(cli: CLI) -> None:
    result = cli("gizmo", "echo", "--input", "-", "--json", stdin="text: piped\nflag: true\n")

    assert result.exit_code == 0, result.output
    assert answer(result.stdout) == echoed(EchoRequest(text="piped", flag=True))


@pytest.mark.parametrize("content", ["[1, 2]", "text: [unclosed", ""])
def test_an_input_that_is_not_a_request_is_a_usage_error(
    cli: CLI, tmp_path: Path, content: str
) -> None:
    request = tmp_path / "request.yaml"
    request.write_text(content)

    result = cli("gizmo", "echo", "--input", request)

    assert result.exit_code == 2
    assert "request.yaml" in result.output


def test_a_secret_is_asked_for_hidden_rather_than_given_as_an_option(cli: CLI) -> None:
    result = cli("gizmo", "echo", "--text", "hi", "--token", "--json", stdin="s3cret\n")

    assert result.exit_code == 0, result.output
    assert answer(result.stdout).token_length == len("s3cret")
    assert "s3cret" not in result.output


def test_a_secret_can_come_from_the_input(cli: CLI, tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"text": "hi", "token": "abc"}))

    result = cli("gizmo", "echo", "--input", request, "--json")

    assert answer(result.stdout).token_length == 3


def test_without_json_the_response_is_rendered(cli: CLI) -> None:
    result = cli("gizmo", "echo", "--text", "hello there", "--tags", "x", "--point", '{"x": 4}')

    assert result.exit_code == 0, result.output
    assert "hello there" in result.stdout
    assert "Token length" in result.stdout
    assert "x: 4" in result.stdout


def fresh_greeter() -> type[SitePlugin]:
    """A plugin whose request model is a class of its own each time: as a local plugin file's
    is, since each service that discovers the plugins imports the file again."""

    class GreetRequest(BaseModel):
        model_config = ConfigDict(use_attribute_docstrings=True)

        name: str
        """Who to greet."""

    class Greeting(BaseModel):
        text: str

    async def greet(service: UDLService, request: GreetRequest) -> Greeting:
        """Greet someone."""
        return Greeting(text=f"Hello, {request.name}!")

    class Greeter(SitePlugin):
        name = "greeter"

        @classmethod
        def operations(cls) -> list[Operation]:
            return [Operation.of(greet, name="greet", title="Greet")]

    return Greeter


def test_a_command_runs_on_a_service_that_imported_the_plugin_again(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    make = harness.service

    def again(loaded: Settings | None = None) -> UDLService:
        harness.registry = registry()
        harness.registry.register(SITES, fresh_greeter())
        return make(loaded)

    harness.service = again

    result = CLI(harness)("greeter", "greet", "--name", "Dan", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"text": "Hello, Dan!"}


def test_a_udl_error_is_shown_with_its_hint_and_exits_with_1(cli: CLI) -> None:
    result = cli("gizmo", "lookup", "--key", "nope")

    assert result.exit_code == 1
    assert "No 'nope' here" in result.output
    assert "Only 'known' is known." in result.output


def test_the_plugin_s_commands_are_documented(cli: CLI) -> None:
    group = cli("gizmo", "--help")
    command = cli("gizmo", "echo", "--help")
    udl = cli("--help")

    assert group.exit_code == 0, group.output
    assert "echo" in group.stdout
    assert "Echo a request back" in group.stdout
    assert "--colour" in command.stdout
    assert "One of a few." in command.stdout
    assert "--input" in command.stdout
    assert "gizmo" in udl.stdout


def test_a_url_is_still_downloaded_when_a_plugin_adds_commands(cli: CLI) -> None:
    result = cli("https://example.com/a", "--json")

    assert result.exit_code == 0, result.output
    assert '"finished"' in result.stdout


# ---------------------------------------------------------------------------------------------
# The API: a route per operation, typed in the OpenAPI schema
# ---------------------------------------------------------------------------------------------


def test_udl_s_own_operations_have_routes_written_by_hand() -> None:
    with pytest.raises(ValueError, match="download"):
        api_path(operations()["download"])


@pytest.fixture
async def api(harness: Harness) -> AsyncIterator[httpx.AsyncClient]:
    async with held(api_client_for(harness)) as client:
        yield client


async def test_the_route_takes_the_request_and_gives_the_response(
    api: httpx.AsyncClient,
) -> None:
    response = await api.post(ECHO, json={"text": "hi", "colour": "blue", "token": "xyz"})

    assert response.status_code == 200
    assert answer(response.text) == echoed(
        EchoRequest.model_validate({"text": "hi", "colour": "blue", "token": "xyz"})
    )


async def test_the_route_is_typed_and_documented(api: httpx.AsyncClient) -> None:
    schema = (await api.get("/openapi.json")).json()

    route = schema["paths"][ECHO]["post"]
    assert (
        route["summary"] == "Echo a request back: every field it was given, as udl understood it."
    )
    assert route["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/EchoRequest"
    )
    ok = route["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok["$ref"].endswith("/Echoed")
    assert "description" in schema["components"]["schemas"]["EchoRequest"]["properties"]["colour"]


async def test_a_bad_request_is_a_422(api: httpx.AsyncClient) -> None:
    response = await api.post(ECHO, json={"count": 1})

    assert response.status_code == 422


async def test_a_udl_error_is_answered_as_error_info(api: httpx.AsyncClient) -> None:
    response = await api.post("/sites/gizmo/lookup", json={"key": "nope"})

    assert response.status_code == 404
    assert ErrorInfo.model_validate(response.json()).code is ErrorCode.DOES_NOT_EXIST


# ---------------------------------------------------------------------------------------------
# The same request, the same response, whichever interface
# ---------------------------------------------------------------------------------------------

_words = st.text(st.characters(codec="utf-8", exclude_characters="\x00"), max_size=20)
_json: st.SearchStrategy[JsonValue] = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | _words,
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(_words, inner, max_size=3),
    max_leaves=6,
)
_requests: st.SearchStrategy[dict[str, Any]] = st.fixed_dictionaries(
    {"text": _words},
    optional={
        "count": st.integers(-(10**12), 10**12),
        "flag": st.booleans(),
        "colour": st.sampled_from([colour.value for colour in Colour]),
        "path": st.from_regex(r"[a-z]{1,8}(/[a-z]{1,8}){0,2}", fullmatch=True),
        "tags": st.lists(_words, max_size=3),
        "sizes": st.lists(st.integers(-1000, 1000), max_size=3),
        "point": st.fixed_dictionaries({"x": st.integers(-9, 9)}, optional={"y": st.integers()}),
        "token": st.text(st.characters(codec="ascii", categories=["L", "N"]), min_size=1),
        "extra": _json,
        "limit": st.integers(1, 10),
    },
)


def _cli_args(raw: dict[str, Any]) -> tuple[list[str], str | None]:
    """The command line (and stdin) that asks for `raw`."""
    args, stdin = ["gizmo", "echo", "--json"], None
    for name, value in raw.items():
        option = f"--{name}"
        if name == "token":
            args.append(option)
            stdin = f"{value}\n"
        elif name == "flag":
            args.append(option if value else "--no-flag")
        elif isinstance(value, list) and name in ("tags", "sizes"):
            args += [f"{option}={item}" for item in cast(list[object], value)]
        elif name in ("point", "extra"):
            args.append(f"{option}={json.dumps(value)}")
        else:
            args.append(f"{option}={value}")
    return args, stdin


async def _through_the_api(harness: Harness, raw: dict[str, Any]) -> JsonValue:
    async with api_client_for(harness) as client:
        response = await client.post(ECHO, json=raw)
    assert response.status_code == 200, response.text
    return response.json()


async def _through_mcp(harness: Harness, raw: dict[str, Any]) -> JsonValue:
    client: Client
    async with mcp_client_for(harness) as client:
        result = await client.call_tool("gizmo_echo", raw)
    assert not result.is_error, result.content
    return result.structured_content


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(raw=_requests)
def test_the_same_request_gives_the_same_response_through_each_interface(
    tmp_path_factory: pytest.TempPathFactory, raw: dict[str, Any]
) -> None:
    harness = gizmo_harness(tmp_path_factory.mktemp("echo"))
    args, stdin = _cli_args(raw)

    result = CLI(harness)(*args, stdin=stdin)
    assert result.exit_code == 0, result.output
    expected = echoed(EchoRequest.model_validate(raw)).model_dump(mode="json")

    assert json.loads(result.stdout) == expected
    assert asyncio.run(_through_the_api(harness, raw)) == expected
    assert asyncio.run(_through_mcp(harness, raw)) == expected
