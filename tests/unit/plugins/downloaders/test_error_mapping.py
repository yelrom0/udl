"""
-------------------------
- test_error_mapping.py -
-------------------------

How each tool's failures map onto the error taxonomy (planning.md, Phase 2): raw tool errors
(HTTP statuses, httpx exceptions, yt-dlp messages, gallery-dl exit statuses and log messages,
curl / wget exit codes, Chromium network errors) to UDLError subclasses
"""

# System Imports
from datetime import UTC, datetime

# Package Imports
import httpx
import pytest

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
from udl.plugins.downloaders.errors import (
    chromium_error,
    curl_error,
    gallerydl_error,
    http_status_error,
    httpx_error,
    parse_retry_after,
    wget_error,
    ytdlp_error,
)

NOW = datetime(2026, 10, 21, 7, 28, 0, tzinfo=UTC)


def assert_maps(error: DownloadError, expected: type[DownloadError], tool: str) -> None:
    assert type(error) is expected, f"{error.message!r} became {type(error).__name__}"
    assert error.tool == tool
    assert error.message


# ---------------------------------------------------------------------------------------------
# HTTP statuses
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthRequired),
        (403, AuthRequired),
        (404, NotFound),
        (410, NotFound),
        (451, NotFound),
        (429, RateLimited),
        (408, NetworkError),
        (500, NetworkError),
        (502, NetworkError),
        (503, NetworkError),
        (504, NetworkError),
        (520, NetworkError),
        (400, ExtractionFailed),
        (405, ExtractionFailed),
        (416, ExtractionFailed),
    ],
)
def test_an_error_status_maps_to_an_error(status: int, expected: type[DownloadError]) -> None:
    error = http_status_error(status, "https://example.com/a.mp4", tool="http")

    assert error is not None
    assert_maps(error, expected, "http")
    assert str(status) in error.message


@pytest.mark.parametrize("status", [200, 204, 206, 301, 302, 304])
def test_a_status_that_is_not_an_error_maps_to_none(status: int) -> None:
    assert http_status_error(status, "https://example.com/", tool="http") is None


def test_429_keeps_retry_after() -> None:
    error = http_status_error(429, "u", tool="http", retry_after="30")

    assert isinstance(error, RateLimited)
    assert error.retry_after == 30.0


def test_503_with_retry_after_is_rate_limiting() -> None:
    error = http_status_error(503, "u", tool="http", retry_after="5")

    assert isinstance(error, RateLimited)
    assert error.retry_after == 5.0


def test_auth_errors_say_how_to_sign_in() -> None:
    error = http_status_error(401, "u", tool="http")

    assert error is not None
    assert error.hint is not None
    assert "cookies" in error.hint


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("120", 120.0),
        (" 5 ", 5.0),
        ("0", 0.0),
        ("Wed, 21 Oct 2026 07:28:30 GMT", 30.0),
        ("Wed, 21 Oct 2026 07:00:00 GMT", 0.0),  # already past
        ("Wed, 21 Oct 2026 07:28:10 -0000", 10.0),  # no timezone: UTC
        ("-5", None),
        ("soon", None),
        ("", None),
        (None, None),
    ],
)
def test_retry_after_is_seconds_or_an_http_date(value: str | None, seconds: float | None) -> None:
    assert parse_retry_after(value, now=NOW) == seconds


# ---------------------------------------------------------------------------------------------
# httpx
# ---------------------------------------------------------------------------------------------

REQUEST = httpx.Request("GET", "https://example.com/a.mp4")


def status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    response = httpx.Response(status, headers=headers, request=REQUEST)
    return httpx.HTTPStatusError("failed", request=REQUEST, response=response)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("[Errno 111] Connection refused", request=REQUEST), NetworkError),
        (httpx.ReadTimeout("timed out", request=REQUEST), NetworkError),
        (httpx.ConnectTimeout("timed out", request=REQUEST), NetworkError),
        (httpx.RemoteProtocolError("peer closed", request=REQUEST), NetworkError),
        (httpx.ReadError("reset", request=REQUEST), NetworkError),
        (httpx.ProxyError("proxy", request=REQUEST), NetworkError),
        (httpx.TooManyRedirects("loop", request=REQUEST), ExtractionFailed),
        (httpx.UnsupportedProtocol("ftp", request=REQUEST), UnsupportedURL),
        (httpx.DecodingError("bad gzip", request=REQUEST), ExtractionFailed),
        (status_error(404), NotFound),
        (status_error(403), AuthRequired),
        (status_error(502), NetworkError),
    ],
)
def test_httpx_errors_map_to_errors(exc: httpx.HTTPError, expected: type[DownloadError]) -> None:
    assert_maps(httpx_error(exc, tool="scraper"), expected, "scraper")


def test_an_httpx_429_keeps_retry_after() -> None:
    error = httpx_error(status_error(429, {"Retry-After": "7"}), tool="http")

    assert isinstance(error, RateLimited)
    assert error.retry_after == 7.0


# ---------------------------------------------------------------------------------------------
# yt-dlp
# ---------------------------------------------------------------------------------------------

YTDLP_MESSAGES: list[tuple[str, type[DownloadError]]] = [
    ("ERROR: Unsupported URL: https://example.com/page", UnsupportedURL),
    (
        "ERROR: [generic] 'not-a-url' is not a valid URL. Set --default-search \"ytsearch\" "
        '(or run  yt-dlp "ytsearch:not-a-url" ) to search YouTube',
        UnsupportedURL,
    ),
    (
        "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm your age. This video may be "
        "inappropriate for some users. Use --cookies-from-browser or --cookies for the "
        "authentication.",
        AuthRequired,
    ),
    (
        "ERROR: [youtube] abc: Sign in to confirm you\u2019re not a bot. Use "
        "--cookies-from-browser or --cookies for the authentication.",
        AuthRequired,
    ),
    (
        "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video",
        AuthRequired,
    ),
    (
        "ERROR: [vimeo] 123: This video is only available for registered users. Use --username "
        "and --password, --netrc-cmd, --netrc (vimeo) or --cookies to provide account credentials",
        AuthRequired,
    ),
    (
        "ERROR: [instagram] abc: Requested content is not available, rate-limit reached or login "
        "required. Use --cookies, --cookies-from-browser, --username and --password",
        AuthRequired,
    ),
    ("ERROR: [patreon] 1: This post is for members only", AuthRequired),
    (
        "ERROR: [generic] video: Unable to download webpage: HTTP Error 401: UNAUTHORIZED "
        "(caused by <HTTPError 401: UNAUTHORIZED>)",
        AuthRequired,
    ),
    (
        "ERROR: [youtube] x: Unable to download API page: HTTP Error 429: Too Many Requests "
        "(caused by <HTTPError 429: Too Many Requests>)",
        RateLimited,
    ),
    ("ERROR: [twitter] 123: Rate limit exceeded", RateLimited),
    (
        "ERROR: [generic] gone: Unable to download webpage: HTTP Error 404: NOT FOUND "
        "(caused by <HTTPError 404: NOT FOUND>)",
        NotFound,
    ),
    (
        "ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader",
        NotFound,
    ),
    (
        "ERROR: [youtube] abc: Video unavailable. This video is no longer available because the "
        "YouTube account associated with this video has been terminated.",
        NotFound,
    ),
    ("ERROR: [vimeo] 1: The video does not exist", NotFound),
    (
        "ERROR: [bbc] p0: This video is not available in your country due to rights "
        "restrictions. You might want to use a VPN or a proxy server (with --proxy)",
        NotFound,
    ),
    (
        "ERROR: [generic] x: Unable to download webpage: <urlopen error [Errno 111] Connection "
        "refused> (caused by TransportError('<urlopen error [Errno 111] Connection refused>'))",
        NetworkError,
    ),
    (
        "ERROR: [generic] x: Unable to download webpage: [Errno -2] Name or service not known "
        "(caused by TransportError('[Errno -2] Name or service not known'))",
        NetworkError,
    ),
    ("ERROR: [download] Got error: The read operation timed out", NetworkError),
    ("ERROR: [download] Got error: 1024 bytes read, 2048 more expected", NetworkError),
    (
        "ERROR: [download] Got error: ('Connection aborted.', ConnectionResetError(104, "
        "'Connection reset by peer'))",
        NetworkError,
    ),
    ("ERROR: unable to download video data: HTTP Error 503: Service Unavailable", NetworkError),
    (
        "ERROR: [generic] x: Unable to download webpage: [SSL: CERTIFICATE_VERIFY_FAILED] "
        "certificate verify failed",
        NetworkError,
    ),
    (
        "ERROR: You have requested merging of multiple formats but ffmpeg is not installed. "
        "Aborting due to --abort-on-error",
        ToolUnavailable,
    ),
    (
        "ERROR: Postprocessing: ffprobe and ffmpeg not found. Please install or provide the path "
        "using --ffmpeg-location",
        ToolUnavailable,
    ),
    (
        "ERROR: [youtube] abc: Requested format is not available. Use --list-formats for a list "
        "of available formats",
        ExtractionFailed,
    ),
    ("ERROR: [generic] x: Unable to extract title; please report this issue", ExtractionFailed),
    ("ERROR: [someie] x: No video formats found!", ExtractionFailed),
    ("ERROR: something nobody expected", ExtractionFailed),
]


@pytest.mark.parametrize(("message", "expected"), YTDLP_MESSAGES)
def test_ytdlp_messages_map_to_errors(message: str, expected: type[DownloadError]) -> None:
    error = ytdlp_error(message)

    assert_maps(error, expected, "yt-dlp")
    assert not error.message.startswith("ERROR:")


def test_a_ytdlp_http_status_beats_the_message() -> None:
    error = ytdlp_error(
        "ERROR: [x] y: Unable to download JSON metadata", status=429, retry_after="9"
    )

    assert isinstance(error, RateLimited)
    assert error.retry_after == 9.0


def test_ytdlp_colours_are_removed_from_messages() -> None:
    error = ytdlp_error("\x1b[0;31mERROR:\x1b[0m Unsupported URL: https://example.com/")

    assert error.message == "Unsupported URL: https://example.com/"


def test_a_geo_block_says_so() -> None:
    error = ytdlp_error("ERROR: [x] 1: This video is not available from your location")

    assert isinstance(error, NotFound)
    assert error.hint is not None
    assert "region" in error.hint


# ---------------------------------------------------------------------------------------------
# gallery-dl: a job's exit status (a bit mask) and what it logged
# ---------------------------------------------------------------------------------------------

GALLERYDL_FAILURES: list[tuple[int, list[str], type[DownloadError]]] = [
    (4, ["'404 Not Found' for 'https://example.com/a.jpg'", "Failed to download a.jpg"], NotFound),
    (4, ["'410 Gone' for 'https://example.com/a.jpg'"], NotFound),
    (4, ["'401 UNAUTHORIZED' for 'https://example.com/a.jpg'"], AuthRequired),
    (4, ["'403 Forbidden' for 'https://example.com/a.jpg'"], AuthRequired),
    (4, ["'429 Too Many Requests' for 'https://example.com/a.jpg' (5/5)"], RateLimited),
    (4, ["'503 Service Unavailable' for 'https://example.com/a.jpg' (5/5)"], NetworkError),
    (4, ["HttpError: '404 Not Found' for 'https://example.com/api'"], NotFound),
    (4, ["NotFoundError: Requested gallery could not be found"], NotFound),
    (16, ["AuthenticationError: Invalid login credentials"], AuthRequired),
    (16, ["AuthRequired: 'cookies' needed to access this post"], AuthRequired),
    (
        8,
        ["ChallengeError: Cloudflare challenge (403 Forbidden) for 'https://x.com/'"],
        AuthRequired,
    ),
    (4, ["HTML response", "Failed to download a.jpg"], ExtractionFailed),
    (128, ["Unable to download data:  ConnectionError: Connection refused"], NetworkError),
    (
        4,
        [
            "ConnectionError: HTTPSConnectionPool(host='x.com', port=443): Max retries exceeded "
            "with url: / (Caused by NewConnectionError('Failed to establish a new connection: "
            "[Errno 111] Connection refused'))"
        ],
        NetworkError,
    ),
    (
        4,
        ["ReadTimeout: HTTPSConnectionPool(host='x.com', port=443): Read timed out."],
        NetworkError,
    ),
    (
        32,
        ["FilenameFormatError: Applying filename format string failed (KeyError)"],
        ExtractionFailed,
    ),
    (
        1,
        ["An unexpected error occurred: KeyError - 'x'. Please run gallery-dl again"],
        ExtractionFailed,
    ),
    (4, [], ExtractionFailed),
]


@pytest.mark.parametrize(("status", "messages", "expected"), GALLERYDL_FAILURES)
def test_gallerydl_failures_map_to_errors(
    status: int, messages: list[str], expected: type[DownloadError]
) -> None:
    assert_maps(gallerydl_error(status, messages), expected, "gallery-dl")


def test_a_gallerydl_challenge_suggests_a_browser() -> None:
    error = gallerydl_error(8, ["ChallengeError: Cloudflare challenge (403 Forbidden) for 'u'"])

    assert error.hint is not None
    assert "browser" in error.hint


# ---------------------------------------------------------------------------------------------
# curl and wget (the http downloader's optional backends)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "stderr", "expected"),
    [
        (22, "curl: (22) The requested URL returned error: 404", NotFound),
        (22, "curl: (22) The requested URL returned error: 401", AuthRequired),
        (22, "curl: (22) The requested URL returned error: 429", RateLimited),
        (22, "curl: (22) The requested URL returned error: 500", NetworkError),
        (22, "", ExtractionFailed),
        (6, "curl: (6) Could not resolve host: nowhere.invalid", NetworkError),
        (
            7,
            "curl: (7) Failed to connect to localhost port 1: Couldn't connect to server",
            NetworkError,
        ),
        (28, "curl: (28) Operation timed out after 5000 milliseconds", NetworkError),
        (35, "curl: (35) OpenSSL SSL_connect: Connection reset by peer", NetworkError),
        (56, "curl: (56) Recv failure: Connection reset by peer", NetworkError),
        (18, "curl: (18) end of response with 100 bytes missing", NetworkError),
        (3, "curl: (3) URL rejected: Malformed input to a URL function", UnsupportedURL),
        (1, 'curl: (1) Protocol "gopher" not supported', UnsupportedURL),
        (47, "curl: (47) Maximum (50) redirects followed", ExtractionFailed),
        (67, "curl: (67) Login denied", AuthRequired),
        (23, "curl: (23) Failure writing output to destination", DownloadError),
    ],
)
def test_curl_exit_codes_map_to_errors(
    code: int, stderr: str, expected: type[DownloadError]
) -> None:
    assert_maps(curl_error(code, stderr), expected, "http")


@pytest.mark.parametrize(
    ("code", "stderr", "expected"),
    [
        (8, "http://x/gone.mp4:\n2026-10-23 10:00:00 ERROR 404: NOT FOUND.", NotFound),
        (8, "2026-10-23 10:00:00 ERROR 401: Unauthorized.", AuthRequired),
        (6, "Username/Password Authentication Failed.", AuthRequired),
        (8, "2026-10-23 10:00:00 ERROR 429: Too Many Requests.", RateLimited),
        (8, "2026-10-23 10:00:00 ERROR 503: Service Unavailable.", NetworkError),
        (8, "", ExtractionFailed),
        (4, "failed: Connection refused.", NetworkError),
        (5, "ERROR: cannot verify example.com's certificate", NetworkError),
        (7, "Protocol error", NetworkError),
        (1, "gopher://x: Unsupported scheme 'gopher'.", UnsupportedURL),
        (3, "Cannot write to 'x.mp4' (No space left on device).", DownloadError),
        (2, "wget: unrecognized option", DownloadError),
    ],
)
def test_wget_exit_codes_map_to_errors(
    code: int, stderr: str, expected: type[DownloadError]
) -> None:
    assert_maps(wget_error(code, stderr), expected, "http")


# ---------------------------------------------------------------------------------------------
# Chromium (the browser downloader)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("net::ERR_NAME_NOT_RESOLVED at https://nowhere.invalid/", NetworkError),
        ("Page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:1/", NetworkError),
        ("net::ERR_TIMED_OUT at https://x.com/", NetworkError),
        ("net::ERR_INTERNET_DISCONNECTED at https://x.com/", NetworkError),
        ("net::ERR_CONNECTION_RESET at https://x.com/", NetworkError),
        ("net::ERR_CERT_AUTHORITY_INVALID at https://x.com/", NetworkError),
        ("Page.goto: Timeout 30000ms exceeded.", NetworkError),
        ("net::ERR_TOO_MANY_REDIRECTS at https://x.com/", ExtractionFailed),
        ("Page.goto: Download is starting", UnsupportedURL),
        ("net::ERR_ABORTED at https://x.com/file.zip", UnsupportedURL),
        ("Target page, context or browser has been closed", ExtractionFailed),
    ],
)
def test_chromium_errors_map_to_errors(message: str, expected: type[DownloadError]) -> None:
    assert_maps(chromium_error(message), expected, "browser")
