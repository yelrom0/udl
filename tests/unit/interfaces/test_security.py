"""
--------------------
- test_security.py -
--------------------

What the API and the MCP server allow: output dirs only under api.allowed_output_roots (for the
operations they call: current_caller), and who a request is — the server's owner (its API key,
or nothing when it has none) or an agent with its own token
"""

# System Imports
from collections.abc import Iterator
from pathlib import Path
from typing import Final

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from udl.config import APISettings, Settings
from udl.core.operations import Caller, current_caller
from udl.core.paths import check_output_dir
from udl.errors import NotAllowed, Unauthorized
from udl.interfaces.security import (
    OWNER,
    Principal,
    PrincipalKind,
    api_key_of,
    authenticate,
)


def roots(*paths: Path) -> Settings:
    return Settings(api=APISettings(allowed_output_roots=list(paths)))


@pytest.fixture(autouse=True)
def called_through_the_api() -> Iterator[None]:
    called = current_caller.set(Caller.API)
    yield
    current_caller.reset(called)


@pytest.mark.parametrize("caller", [Caller.API, Caller.MCP, Caller.AUTOMATION])
def test_the_servers_and_automations_paths_are_checked(tmp_path: Path, caller: Caller) -> None:
    current_caller.set(caller)

    with pytest.raises(NotAllowed):
        check_output_dir(roots(tmp_path / "allowed"), tmp_path / "elsewhere")


@pytest.mark.parametrize("caller", [Caller.CLI, None])
def test_the_cli_s_and_udl_s_own_paths_are_not(tmp_path: Path, caller: Caller | None) -> None:
    current_caller.set(caller)

    check_output_dir(roots(tmp_path / "allowed"), tmp_path / "elsewhere")


def test_no_output_dir_is_allowed(tmp_path: Path) -> None:
    check_output_dir(roots(tmp_path / "allowed"), None)


@pytest.mark.parametrize("inside", [".", "videos", "videos/new", "videos/../music"])
def test_an_output_dir_under_an_allowed_root_is_allowed(tmp_path: Path, inside: str) -> None:
    config = roots(tmp_path / "other", tmp_path / "allowed")

    check_output_dir(config, tmp_path / "allowed" / inside)


def test_the_home_dir_and_variables_are_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("UDL_TEST_DIR", "allowed")

    check_output_dir(roots(tmp_path / "allowed"), Path("~/$UDL_TEST_DIR/videos"))


@pytest.mark.parametrize("outside", ["..", "../elsewhere", "videos/../../elsewhere"])
def test_an_output_dir_outside_the_allowed_roots_is_not_allowed(
    tmp_path: Path, outside: str
) -> None:
    config = roots(tmp_path / "allowed")

    with pytest.raises(NotAllowed, match="allowed_output_roots") as raised:
        check_output_dir(config, tmp_path / "allowed" / outside)

    assert raised.value.hint is not None
    assert str(tmp_path / "allowed") in raised.value.hint


def test_a_link_out_of_an_allowed_root_is_not_allowed(tmp_path: Path) -> None:
    (tmp_path / "allowed").mkdir()
    (tmp_path / "allowed" / "out").symlink_to(tmp_path / "elsewhere")

    with pytest.raises(NotAllowed):
        check_output_dir(roots(tmp_path / "allowed"), tmp_path / "allowed" / "out" / "videos")


def test_a_relative_output_dir_is_taken_from_the_working_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    check_output_dir(roots(tmp_path), Path("videos"))
    with pytest.raises(NotAllowed):
        check_output_dir(roots(tmp_path / "allowed"), Path("videos"))


def test_without_allowed_roots_no_output_dir_is_allowed(tmp_path: Path) -> None:
    with pytest.raises(NotAllowed):
        check_output_dir(roots(), tmp_path)


def keyed(key: str | None) -> Settings:
    return Settings(api=APISettings(api_key=SecretStr(key) if key else None))


@pytest.mark.parametrize("given", [None, "", "anything"])
async def test_without_an_api_key_every_request_is_the_owner(given: str | None) -> None:
    assert await authenticate(keyed(None), given) == OWNER


async def test_the_owner_is_the_api_key() -> None:
    assert await authenticate(keyed("s3cret"), "s3cret") == OWNER


@pytest.mark.parametrize("given", [None, "", "S3CRET", "s3cret ", "s3cre"])
async def test_with_an_api_key_anything_else_is_unauthorized(given: str | None) -> None:
    with pytest.raises(Unauthorized, match="X-API-Key"):
        await authenticate(keyed("s3cret"), given)


# ---------------------------------------------------------------------------------------------
# An agent's token
# ---------------------------------------------------------------------------------------------

TOKEN: Final = "udl_agent_not-a-real-token"  # noqa: S105 - (a fake's own)
"""An agent's token, as its runner would send it."""
AGENT: Final = Principal(kind=PrincipalKind.AGENT, name="deepseek-a", role="implementer")


class Book:
    """A token book that knows one token (orch's records stand in)."""

    def __init__(self, token: str = TOKEN, principal: Principal | None = AGENT) -> None:
        self.token = token
        self.principal = principal
        self.asked: list[str] = []

    async def principal_for(self, token: str) -> Principal | None:
        """The agent whose token this is (it hashes and looks it up, as orch does)."""
        self.asked.append(token)
        return self.principal if token == self.token else None


async def test_an_agent_s_token_is_the_agent_it_belongs_to() -> None:
    book = Book()

    assert await authenticate(keyed(None), TOKEN, book) == AGENT
    assert await authenticate(keyed("s3cret"), TOKEN, book) == AGENT
    assert book.asked == [TOKEN, TOKEN]  # (the book is asked for the token itself)


async def test_an_agent_s_token_udl_does_not_know_is_unauthorized() -> None:
    with pytest.raises(Unauthorized, match="does not know that agent token"):
        await authenticate(keyed(None), TOKEN, Book(principal=None))


async def test_an_agent_s_token_with_nowhere_to_look_is_unauthorized() -> None:
    with pytest.raises(Unauthorized, match="keeps none"):
        await authenticate(keyed(None), TOKEN)


async def test_an_agents_token_is_not_the_api_key() -> None:
    with pytest.raises(Unauthorized, match="X-API-Key"):
        await authenticate(keyed("s3cret"), "s3cret-not", Book())


def test_the_key_a_request_sends_is_read_from_either_header() -> None:
    """`X-API-Key` or `Authorization: Bearer` — the bearer form is what a client that can send
    one header only (Codex's `bearer_token_env_var`) sends. The header wins when both are."""
    assert api_key_of({"X-API-Key": "s3cret"}) == "s3cret"
    assert api_key_of({"Authorization": "Bearer s3cret"}) == "s3cret"
    assert api_key_of({"authorization": "bearer s3cret"}) == "s3cret"
    assert api_key_of({"X-API-Key": "one", "Authorization": "Bearer two"}) == "one"
    assert api_key_of({"x-api-key": "s3cret"}) == "s3cret"  # (either case: a plain mapping)
    assert api_key_of({"AUTHORIZATION": "Bearer s3cret"}) == "s3cret"
    assert api_key_of({"Authorization": "Basic s3cret"}) is None
    assert api_key_of({}) is None
    assert api_key_of({"Authorization": "Bearer "}) is None


def test_an_agents_token_is_hashed_the_way_the_book_does_it() -> None:
    """(orch's AgentToken keeps the SHA-256, so the same token is looked up by the same hash.)"""
    from udl.plugins.apps.orch.records import hash_token

    digest = hash_token(TOKEN)

    assert digest == hash_token(TOKEN)
    assert digest != hash_token(f"{TOKEN} ")
    assert len(digest) == 64
    assert TOKEN not in digest
