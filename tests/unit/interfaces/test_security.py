"""
--------------------
- test_security.py -
--------------------

What the API and the MCP server allow: output dirs only under api.allowed_output_roots, and only
requests with the API key when one is set
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from udl.config import APISettings, Settings
from udl.errors import NotAllowed, Unauthorized
from udl.interfaces.security import check_api_key, check_output_dir


def roots(*paths: Path) -> Settings:
    return Settings(api=APISettings(allowed_output_roots=list(paths)))


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
def test_without_an_api_key_every_request_is_allowed(given: str | None) -> None:
    check_api_key(keyed(None), given)


def test_with_an_api_key_a_request_with_it_is_allowed() -> None:
    check_api_key(keyed("s3cret"), "s3cret")


@pytest.mark.parametrize("given", [None, "", "S3CRET", "s3cret ", "s3cre"])
def test_with_an_api_key_a_request_without_it_is_unauthorized(given: str | None) -> None:
    with pytest.raises(Unauthorized, match="X-API-Key"):
        check_api_key(keyed("s3cret"), given)
