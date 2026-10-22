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
    Cancelled,
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
    error_from_info,
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
    "error", [ConfigError, PluginError, DatabaseError, DownloadError, NoStrategyFound, Cancelled]
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
        "retry_after": None,
    }


def test_errors_outside_downloaders_have_no_tool() -> None:
    assert NoStrategyFound("every tool failed").to_info().tool is None


def test_rate_limited_keeps_retry_after() -> None:
    assert RateLimited("slow down", retry_after=30.0).retry_after == 30.0
    assert RateLimited("slow down").retry_after is None


def test_rate_limited_info_keeps_retry_after() -> None:
    assert RateLimited("slow down", retry_after=30.0).to_info().retry_after == 30.0
    assert AuthRequired("sign in").to_info().retry_after is None


def test_cancelled_is_not_a_download_error() -> None:
    # The resolver tries the next tool on a DownloadError, but a cancelled download must stop.
    assert not issubclass(Cancelled, DownloadError)


@pytest.mark.parametrize(
    "error",
    [
        UDLError("a"),
        ConfigError("b", hint="fix it"),
        DatabaseError("c"),
        DownloadError("d", tool="http"),
        UnsupportedURL("e", tool="yt-dlp", hint="try another tool"),
        AuthRequired("f", tool="scraper"),
        RateLimited("g", retry_after=2.5, tool="http"),
        RateLimited("h"),
        NotFound("i"),
        Cancelled("j"),
        NoStrategyFound("k", hint="define a tag chain"),
    ],
)
def test_an_error_is_rebuilt_from_its_info(error: UDLError) -> None:
    rebuilt = error_from_info(error.to_info())

    assert type(rebuilt) is type(error)
    assert rebuilt.to_info() == error.to_info()


def test_every_code_rebuilds_its_own_class() -> None:
    classes = [UDLError, *_all_subclasses(UDLError)]

    for cls in classes:
        info = ErrorInfo(code=cls.code, message="m")

        assert type(error_from_info(info)) is cls
