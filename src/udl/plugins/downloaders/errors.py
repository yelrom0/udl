"""
-------------
- errors.py -
-------------

How each tool's failures map onto the error taxonomy (planning.md, Phase 2): HTTP statuses,
httpx exceptions, yt-dlp messages, gallery-dl exit statuses and log messages, curl / wget exit
codes and Chromium network errors. The downloaders raise what these return, so the resolver
reacts to every tool's failures the same way.
"""

# System Imports
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

# Package Imports
import httpx

# Local Imports
from udl.errors import (
    AuthRequired,
    DownloadError,
    ExtractionFailed,
    NetworkError,
    NotFound,
    RateLimited,
    ToolUnavailable,
    UnsupportedURL,
)

AUTH_HINT: Final[str] = (
    "The site wants you signed in: sign in with `udl browser login <url>`, or set "
    "cookies_from_browser in the config so udl uses your browser's cookies."
)
RATE_LIMIT_HINT: Final[str] = "The site is limiting requests. Wait a while, then try again."
GEO_HINT: Final[str] = "The site blocks your region; a VPN or proxy may help."
CHALLENGE_HINT: Final[str] = (
    "The site shows a challenge (e.g. Cloudflare), which needs a real browser: try the browser "
    "downloader, after `udl browser login <url>`."
)


def _search(pattern: str, text: str) -> re.Match[str] | None:
    return re.search(pattern, text, re.IGNORECASE)


# ---------------------------------------------------------------------------------------------
# HTTP statuses
# ---------------------------------------------------------------------------------------------


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """A Retry-After header as seconds to wait: either a number of seconds or an HTTP date."""
    if value is None or not (value := value.strip()):
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - (now or datetime.now(UTC))).total_seconds(), 0.0)


def http_status_error(
    status: int,
    url: str,
    *,
    tool: str,
    retry_after: str | None = None,
    message: str | None = None,
) -> DownloadError | None:
    """The error an HTTP status means, or None when it is not an error (below 400).
    `message` replaces the default one (e.g. with the tool's own)."""
    if status < 400:
        return None
    message = message or f"HTTP {status} for {url}"
    wait = parse_retry_after(retry_after)
    if status in (401, 403):
        return AuthRequired(message, tool=tool, hint=AUTH_HINT)
    if status in (404, 410, 451):
        return NotFound(message, tool=tool)
    if status == 429 or (status == 503 and wait is not None):
        return RateLimited(message, retry_after=wait, tool=tool, hint=RATE_LIMIT_HINT)
    if status == 408 or status >= 500:
        return NetworkError(message, tool=tool)
    return ExtractionFailed(message, tool=tool)


def _status_in(text: str, pattern: str) -> int | None:
    match = _search(pattern, text)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------------------------
# httpx (http, scraper)
# ---------------------------------------------------------------------------------------------


def httpx_error(exc: httpx.HTTPError, *, tool: str) -> DownloadError:
    """The error an httpx exception means."""
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        error = http_status_error(
            response.status_code,
            str(exc.request.url),
            tool=tool,
            retry_after=response.headers.get("Retry-After"),
        )
        return error or ExtractionFailed(str(exc), tool=tool)
    message = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, httpx.UnsupportedProtocol):
        return UnsupportedURL(message, tool=tool)
    if isinstance(exc, httpx.TooManyRedirects | httpx.DecodingError):
        return ExtractionFailed(message, tool=tool)
    return NetworkError(message, tool=tool)


# ---------------------------------------------------------------------------------------------
# yt-dlp: its messages, and the HTTP status of the error that caused them
# ---------------------------------------------------------------------------------------------

_ANSI: Final = re.compile(r"\x1b\[[0-9;]*m")

_YTDLP_UNSUPPORTED: Final = r"unsupported url|is not a valid url|no suitable (?:extractor|info)"
_YTDLP_NO_FFMPEG: Final = r"ffmpeg (?:is )?not (?:installed|found)|ffprobe and ffmpeg not found"
_YTDLP_AUTH: Final = (
    r"sign in|log ?in(?: is)? required|logged.in|--cookies|--username|members.only"
    r"|registered users|private video|requires? (?:authentication|an account)|account credentials"
)
_YTDLP_RATE_LIMIT: Final = r"too many requests|rate.?limit"
_YTDLP_GEO: Final = (
    r"not (?:be )?available (?:in|from) your (?:country|location|region)|geo.?restrict"
    r"|blocked in your country"
)
_YTDLP_GONE: Final = (
    r"video unavailable|has been removed|does not exist|no longer available|has been deleted"
    r"|content is not available"
)
_NETWORK: Final = (
    r"connection (?:refused|reset|aborted)|timed out|name or service not known"
    r"|temporary failure in name resolution|network is unreachable|getaddrinfo failed"
    r"|bytes read, \d+ more expected|incompleteread|\bssl\b|certificate|remote end closed"
    r"|max retries exceeded|newconnectionerror|connectionerror|readtimeout"
)


def ytdlp_error(
    message: str, *, status: int | None = None, retry_after: str | None = None
) -> DownloadError:
    """The error a yt-dlp failure means: its message, and (when an HTTP error caused it) the
    status. yt-dlp has few exception types of its own, so the message carries the meaning."""
    tool = "yt-dlp"
    text = _ANSI.sub("", message).strip().removeprefix("ERROR:").strip()

    if status is not None and (
        error := http_status_error(status, "", tool=tool, retry_after=retry_after, message=text)
    ):
        return error
    if _search(_YTDLP_UNSUPPORTED, text):
        return UnsupportedURL(text, tool=tool)
    if _search(_YTDLP_NO_FFMPEG, text):
        return ToolUnavailable(
            text, tool=tool, hint="Install ffmpeg, e.g. `sudo pacman -S ffmpeg`."
        )
    if _search(_YTDLP_AUTH, text):
        return AuthRequired(text, tool=tool, hint=AUTH_HINT)
    if (code := _status_in(text, r"HTTP Error (\d{3})")) and (
        error := http_status_error(code, "", tool=tool, retry_after=retry_after, message=text)
    ):
        return error
    if _search(_YTDLP_RATE_LIMIT, text):
        return RateLimited(text, tool=tool, hint=RATE_LIMIT_HINT)
    if _search(_YTDLP_GEO, text):
        return NotFound(text, tool=tool, hint=GEO_HINT)
    if _search(_YTDLP_GONE, text):
        return NotFound(text, tool=tool)
    if _search(_NETWORK, text):
        return NetworkError(text, tool=tool)
    return ExtractionFailed(text, tool=tool)


# ---------------------------------------------------------------------------------------------
# gallery-dl: a job's exit status (a bit mask) and the warnings / errors it logged
# ---------------------------------------------------------------------------------------------

# gallery_dl.exception codes, as bits of a job's status.
_GALLERYDL_CHALLENGE: Final = 8
_GALLERYDL_AUTH: Final = 16
_GALLERYDL_OS_ERROR: Final = 128


def gallerydl_error(status: int, messages: Sequence[str]) -> DownloadError:
    """The error a failed gallery-dl job (non-zero `status`) means, given what it logged."""
    tool = "gallery-dl"
    text = " | ".join(messages) or f"gallery-dl failed (exit status {status})"

    if status & _GALLERYDL_CHALLENGE or _search(r"ChallengeError", text):
        return AuthRequired(text, tool=tool, hint=CHALLENGE_HINT)
    if (code := _status_in(text, r"'(\d{3}) [^']*' for '")) and (
        error := http_status_error(code, "", tool=tool, message=text)
    ):
        return error
    if status & _GALLERYDL_AUTH or _search(r"Authentication|Authorization|AuthRequired", text):
        return AuthRequired(text, tool=tool, hint=AUTH_HINT)
    if _search(r"NotFoundError|could not be found", text):
        return NotFound(text, tool=tool)
    if status & _GALLERYDL_OS_ERROR or _search(_NETWORK, text):
        return NetworkError(text, tool=tool)
    return ExtractionFailed(text, tool=tool)


# ---------------------------------------------------------------------------------------------
# curl and wget: the http downloader's optional backends
# ---------------------------------------------------------------------------------------------

# curl's exit codes (man curl, "EXIT CODES").
_CURL_UNSUPPORTED: Final = frozenset({1, 3})
_CURL_NETWORK: Final = frozenset({5, 6, 7, 8, 16, 18, 28, 35, 52, 55, 56, 60, 92, 95})
_CURL_HTTP_ERROR: Final = 22
_CURL_TOO_MANY_REDIRECTS: Final = 47
_CURL_LOGIN_DENIED: Final = 67


def curl_error(code: int, stderr: str) -> DownloadError:
    """The error a curl exit code (run with --fail) means, given its stderr."""
    tool = "http"
    text = stderr.strip() or f"curl failed (exit code {code})"
    if code == _CURL_HTTP_ERROR:
        status = _status_in(text, r"returned error: (\d{3})")
        error = http_status_error(status, "", tool=tool, message=text) if status else None
        return error or ExtractionFailed(text, tool=tool)
    if code in _CURL_UNSUPPORTED:
        return UnsupportedURL(text, tool=tool)
    if code in _CURL_NETWORK:
        return NetworkError(text, tool=tool)
    if code == _CURL_TOO_MANY_REDIRECTS:
        return ExtractionFailed(text, tool=tool)
    if code == _CURL_LOGIN_DENIED:
        return AuthRequired(text, tool=tool, hint=AUTH_HINT)
    return DownloadError(text, tool=tool)


# wget's exit codes (man wget, "EXIT STATUS").
_WGET_NETWORK: Final = frozenset({4, 5, 7})
_WGET_AUTH: Final = 6
_WGET_SERVER_ERROR: Final = 8


def wget_error(code: int, stderr: str) -> DownloadError:
    """The error a wget exit status means, given its stderr."""
    tool = "http"
    text = stderr.strip() or f"wget failed (exit status {code})"
    if code == _WGET_SERVER_ERROR:
        status = _status_in(text, r"ERROR (\d{3})")
        error = http_status_error(status, "", tool=tool, message=text) if status else None
        return error or ExtractionFailed(text, tool=tool)
    if code == _WGET_AUTH:
        return AuthRequired(text, tool=tool, hint=AUTH_HINT)
    if code in _WGET_NETWORK:
        return NetworkError(text, tool=tool)
    if _search(r"unsupported scheme", text):
        return UnsupportedURL(text, tool=tool)
    return DownloadError(text, tool=tool)


# ---------------------------------------------------------------------------------------------
# Chromium (the browser downloader), as Playwright reports it
# ---------------------------------------------------------------------------------------------


def chromium_error(message: str) -> DownloadError:
    """The error a failed page load in Chromium means."""
    tool = "browser"
    text = message.strip()
    if _search(r"Download is starting|ERR_ABORTED", text):
        # Chromium downloads the URL instead of showing it: a file, for the http downloader.
        return UnsupportedURL(text, tool=tool, hint="The URL is a file download; try http.")
    if _search(r"ERR_TOO_MANY_REDIRECTS", text):
        return ExtractionFailed(text, tool=tool)
    if _search(r"net::ERR_|Timeout \d+ms exceeded", text):
        return NetworkError(text, tool=tool)
    return ExtractionFailed(text, tool=tool)
