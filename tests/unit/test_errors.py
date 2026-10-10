"""
------------------
- test_errors.py -
------------------

The UDLError hierarchy: codes, the error taxonomy flags and serialisation
"""

# System Imports
import json

# Package Imports
import pytest

# Local Imports
from udl.errors import (
    AuthRequired,
    ConfigError,
    DatabaseError,
    DownloadError,
    ErrorCode,
    ErrorInfo,
    ExtractionFailed,
    NetworkError,
    NoStrategyFound,
    NotFound,
    PluginError,
    RateLimited,
    ToolUnavailable,
    UDLError,
    UnsupportedURL,
)


def _all_subclasses(cls: type[UDLError]) -> list[type[UDLError]]:
    found: list[type[UDLError]] = []
    for sub in cls.__subclasses__():
        found += [sub, *_all_subclasses(sub)]
    return found


def test_every_error_has_its_own_code() -> None:
    classes = [UDLError, *_all_subclasses(UDLError)]

    codes = [cls.code for cls in classes]

    assert len(codes) == len(set(codes))
    assert set(codes) == set(ErrorCode)


@pytest.mark.parametrize(
    ("error", "counts_against_strategy", "retryable"),
    [
        # The error taxonomy table in planning.md (Phase 2).
        (UnsupportedURL, True, False),
        (ExtractionFailed, True, False),
        (AuthRequired, True, False),
        (RateLimited, False, True),
        (NetworkError, False, True),
        (NotFound, False, False),
        (ToolUnavailable, False, False),
    ],
)
def test_download_errors_tell_the_resolver_how_to_react(
    error: type[DownloadError], counts_against_strategy: bool, retryable: bool
) -> None:
    assert issubclass(error, DownloadError)
    assert error.counts_against_strategy is counts_against_strategy
    assert error.retryable is retryable


@pytest.mark.parametrize(
    "error", [ConfigError, PluginError, DatabaseError, DownloadError, NoStrategyFound]
)
def test_every_error_can_be_caught_as_a_udl_error(error: type[UDLError]) -> None:
    with pytest.raises(UDLError, match="went wrong"):
        raise error("went wrong")


def test_str_is_the_message() -> None:
    assert str(ConfigError("bad config", hint="check the file")) == "bad config"


def test_to_info_is_json_ready() -> None:
    error = AuthRequired("Sign in to continue", tool="yt-dlp", hint="udl browser login <url>")

    info = error.to_info()

    assert info == ErrorInfo(
        code=ErrorCode.AUTH_REQUIRED,
        message="Sign in to continue",
        hint="udl browser login <url>",
        tool="yt-dlp",
    )
    assert json.loads(info.model_dump_json()) == {
        "code": "auth_required",
        "message": "Sign in to continue",
        "hint": "udl browser login <url>",
        "tool": "yt-dlp",
    }


def test_errors_outside_downloaders_have_no_tool() -> None:
    assert NoStrategyFound("every tool failed").to_info().tool is None


def test_rate_limited_keeps_retry_after() -> None:
    assert RateLimited("slow down", retry_after=30.0).retry_after == 30.0
    assert RateLimited("slow down").retry_after is None
