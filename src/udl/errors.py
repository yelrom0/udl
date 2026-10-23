"""
-------------
- errors.py -
-------------

The UDLError hierarchy: every error udl raises on purpose
"""

# System Imports
from enum import StrEnum
from typing import ClassVar

# Package Imports
from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    """A stable, machine-readable code for each error type."""

    UDL_ERROR = "udl_error"
    CONFIG_ERROR = "config_error"
    PLUGIN_ERROR = "plugin_error"
    DATABASE_ERROR = "database_error"
    DOWNLOAD_ERROR = "download_error"
    UNSUPPORTED_URL = "unsupported_url"
    EXTRACTION_FAILED = "extraction_failed"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    NOT_FOUND = "not_found"
    TOOL_UNAVAILABLE = "tool_unavailable"
    NO_STRATEGY_FOUND = "no_strategy_found"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    DOES_NOT_EXIST = "does_not_exist"


class ErrorInfo(BaseModel):
    """A UDLError as data, for API / MCP responses and --json output."""

    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str
    hint: str | None = None
    tool: str | None = None
    retry_after: float | None = None  # seconds, for rate_limited


class UDLError(Exception):
    """Base class for every error udl raises on purpose. `hint` tells the user what to do next."""

    code: ClassVar[ErrorCode] = ErrorCode.UDL_ERROR

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_info(self) -> ErrorInfo:
        return ErrorInfo(code=self.code, message=self.message, hint=self.hint)


class ConfigError(UDLError):
    """The config file or UDL_* environment variables are invalid."""

    code = ErrorCode.CONFIG_ERROR


class PluginError(UDLError):
    """A plugin could not be loaded or is misconfigured."""

    code = ErrorCode.PLUGIN_ERROR


class DatabaseError(UDLError):
    """A database plugin failed."""

    code = ErrorCode.DATABASE_ERROR


class DownloadError(UDLError):
    """A downloader failed on a URL.

    The class flags tell the resolver how to react (the error taxonomy in planning.md):
    `counts_against_strategy` marks the stored strategy as failing, and `retryable` means the
    same tool may succeed if tried again later.
    """

    code = ErrorCode.DOWNLOAD_ERROR
    counts_against_strategy: ClassVar[bool] = True
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, *, tool: str | None = None, hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.tool = tool

    def to_info(self) -> ErrorInfo:
        return super().to_info().model_copy(update={"tool": self.tool})


class UnsupportedURL(DownloadError):
    """The tool does not handle this URL. Try the next candidate."""

    code = ErrorCode.UNSUPPORTED_URL


class ExtractionFailed(DownloadError):
    """The tool handles this site but could not find the media. Try the next candidate."""

    code = ErrorCode.EXTRACTION_FAILED


class AuthRequired(DownloadError):
    """The site needs a login (401/403, "sign in" messages). Retry with cookies, then a browser."""

    code = ErrorCode.AUTH_REQUIRED


class RateLimited(DownloadError):
    """The site is throttling requests. Back off (see `retry_after`), then retry the same tool."""

    code = ErrorCode.RATE_LIMITED
    counts_against_strategy = False
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        tool: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, tool=tool, hint=hint)
        self.retry_after = retry_after

    def to_info(self) -> ErrorInfo:
        return super().to_info().model_copy(update={"retry_after": self.retry_after})


class NetworkError(DownloadError):
    """A connection problem rather than a problem with the strategy. Retry with backoff."""

    code = ErrorCode.NETWORK_ERROR
    counts_against_strategy = False
    retryable = True


class NotFound(DownloadError):
    """The content is gone (e.g. 404), not the strategy. Stop trying."""

    code = ErrorCode.NOT_FOUND
    counts_against_strategy = False


class ToolUnavailable(DownloadError):
    """The tool's binary or package is missing. Skip it."""

    code = ErrorCode.TOOL_UNAVAILABLE
    counts_against_strategy = False


class NoStrategyFound(UDLError):
    """Every candidate tool failed on a URL."""

    code = ErrorCode.NO_STRATEGY_FOUND


class Cancelled(UDLError):
    """The download was cancelled (by the user, or because udl is stopping).

    Not a DownloadError, so code that tries the next tool after a failure stops instead.
    """

    code = ErrorCode.CANCELLED


class Interrupted(UDLError):
    """udl stopped (e.g. it was killed) before a download it had queued or started ended."""

    code = ErrorCode.INTERRUPTED


class DoesNotExist(UDLError):
    """What was asked for (a download job, a site) does not exist."""

    code = ErrorCode.DOES_NOT_EXIST


def _classes(cls: type[UDLError]) -> list[type[UDLError]]:
    return [cls, *(sub for child in cls.__subclasses__() for sub in _classes(child))]


def error_from_info(info: ErrorInfo) -> UDLError:
    """The error that `info` describes (e.g. a probe result's error), to raise or react to."""
    cls = next((c for c in _classes(UDLError) if c.code == info.code), UDLError)
    if issubclass(cls, RateLimited):
        return cls(info.message, retry_after=info.retry_after, tool=info.tool, hint=info.hint)
    if issubclass(cls, DownloadError):
        return cls(info.message, tool=info.tool, hint=info.hint)
    return cls(info.message, hint=info.hint)
