"""
---------------
- test_http.py -
---------------

The http downloader against the fixture site and servers scripted per test, with each backend
(httpx, curl, wget): what counts as a file, naming, redirects, servers that refuse HEAD, resuming,
headers and cookies
"""

# System Imports
import asyncio
import gzip
import logging
from collections.abc import Callable
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

# Local Imports
from support.fixture_site import FixtureSite, Page
from udl.errors import (
    Cancelled,
    DownloadError,
    ErrorCode,
    ExtractionFailed,
    NetworkError,
    NotFound,
)
from udl.http import USER_AGENT, http_client
from udl.plugins.downloaders.base import CancelToken, Cookie, DownloadContext, ProbeContext
from udl.plugins.downloaders.http import HttpBackend, HttpDownloader, HttpOptions

type Handler = Callable[[Request], Response]


@pytest.fixture(params=list(HttpBackend))
def backend(request: pytest.FixtureRequest) -> HttpBackend:
    return request.param


@pytest.fixture
def http(backend: HttpBackend) -> HttpDownloader:
    return HttpDownloader(HttpOptions(backend=backend))


@pytest.fixture
def sample(fixture_site: FixtureSite) -> bytes:
    return fixture_site.content(Page.DIRECT_FILE)


def serve(
    data: bytes,
    *,
    mimetype: str = "video/mp4",
    ranges: bool = True,
    headers: dict[str, str] | None = None,
) -> Handler:
    """A handler that serves `data`, honouring Range requests when `ranges`."""

    def handler(request: Request) -> Response:
        response = Response(data, mimetype=mimetype, headers=headers)
        if ranges:
            return response.make_conditional(request, accept_ranges=True, complete_length=len(data))
        return response

    return handler


def files_in(folder: Path) -> list[Path]:
    return list(folder.iterdir())


def requests_to(server: HTTPServer, path: str, method: str = "GET") -> list[Request]:
    return [
        request for request, _ in server.log if (request.path, request.method) == (path, method)
    ]


# ---------------------------------------------------------------------------------------------
# What counts as a file
# ---------------------------------------------------------------------------------------------


async def test_a_probe_describes_the_file(http: HttpDownloader, fixture_site: FixtureSite) -> None:
    url = fixture_site.url(Page.DIRECT_FILE)

    result = await http.probe(ProbeContext(url=url))

    assert result.title == "sample"
    [media] = result.media
    assert (media.url, media.content_type, media.title) == (url, "video/mp4", "sample")
    assert media.size_bytes == len(fixture_site.content(Page.DIRECT_FILE))
    assert result.confidence >= 0.8


@pytest.mark.parametrize(
    ("mimetype", "headers", "ok"),
    [
        ("video/mp4", {}, True),
        ("audio/mpeg", {}, True),
        ("image/png", {}, True),
        ("application/octet-stream", {}, True),
        ("application/pdf", {}, True),
        ("text/html", {"Content-Disposition": "attachment; filename=page.html"}, True),
        ("text/html", {}, False),
        ("application/xhtml+xml", {}, False),
    ],
)
async def test_anything_but_a_web_page_is_a_file(
    httpserver: HTTPServer, mimetype: str, headers: dict[str, str], ok: bool
) -> None:
    httpserver.expect_request("/thing").respond_with_handler(
        serve(b"content", mimetype=mimetype, headers=headers)
    )

    result = await HttpDownloader().probe(ProbeContext(url=httpserver.url_for("/thing")))

    assert result.ok is ok
    if not ok:
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSUPPORTED_URL


@pytest.mark.parametrize(
    ("path", "mimetype"),
    [
        ("/live.m3u8", "application/vnd.apple.mpegurl"),
        ("/live.m3u8", "text/plain"),  # a common misconfiguration
        ("/manifest.mpd", "application/dash+xml"),
    ],
)
async def test_a_stream_playlist_is_left_to_yt_dlp(
    httpserver: HTTPServer, path: str, mimetype: str
) -> None:
    httpserver.expect_request(path).respond_with_handler(serve(b"#EXTM3U\n", mimetype=mimetype))

    result = await HttpDownloader().probe(ProbeContext(url=httpserver.url_for(path)))

    assert result.error is not None
    assert result.error.code is ErrorCode.UNSUPPORTED_URL
    assert result.error.hint is not None
    assert "yt-dlp" in result.error.hint


async def test_redirects_are_followed(
    http: HttpDownloader,
    httpserver: HTTPServer,
    fixture_site: FixtureSite,
    sample: bytes,
    tmp_path: Path,
) -> None:
    target = fixture_site.url(Page.DIRECT_FILE)
    httpserver.expect_request("/latest").respond_with_data(
        "", status=302, headers={"Location": target}
    )
    url = httpserver.url_for("/latest")

    probed = await http.probe(ProbeContext(url=url))
    result = await http.download(DownloadContext(url=url, output_dir=tmp_path))

    assert [media.url for media in probed.media] == [target]
    assert result.files == [tmp_path / "sample.mp4"]
    assert result.files[0].read_bytes() == sample


async def test_when_head_is_refused_the_first_byte_is_asked_for(
    http: HttpDownloader, httpserver: HTTPServer, sample: bytes, tmp_path: Path
) -> None:
    httpserver.expect_request("/clip.mp4", method="HEAD").respond_with_data("", status=405)
    httpserver.expect_request("/clip.mp4", method="GET").respond_with_handler(serve(sample))
    url = httpserver.url_for("/clip.mp4")

    probed = await http.probe(ProbeContext(url=url))
    result = await http.download(DownloadContext(url=url, output_dir=tmp_path))

    assert [media.size_bytes for media in probed.media] == [len(sample)]
    assert requests_to(httpserver, "/clip.mp4")[0].headers["Range"] == "bytes=0-0"
    assert result.files[0].read_bytes() == sample


@pytest.mark.parametrize(("path", "confidence"), [("/clip.mp4", 0.6), ("/thing", 0.4)])
async def test_without_a_type_the_extension_says_how_likely_a_file_is(
    httpserver: HTTPServer, sample: bytes, path: str, confidence: float
) -> None:
    def untyped(request: Request) -> Response:
        response = serve(sample)(request)
        del response.headers["Content-Type"]
        return response

    httpserver.expect_request(path).respond_with_handler(untyped)

    result = await HttpDownloader().probe(ProbeContext(url=httpserver.url_for(path)))

    assert result.ok is True
    assert result.confidence == confidence


async def test_an_unreachable_server_is_a_network_error(
    backend: HttpBackend, tmp_path: Path
) -> None:
    http = HttpDownloader(HttpOptions(backend=backend, retries=0))
    url = "http://127.0.0.1:1/clip.mp4"  # nothing listens on port 1

    probed = await http.probe(ProbeContext(url=url))

    assert probed.error is not None
    assert probed.error.code is ErrorCode.NETWORK_ERROR
    with pytest.raises(NetworkError):
        await http.download(DownloadContext(url=url, output_dir=tmp_path))


async def test_a_download_refused_after_the_probe_leaves_no_file(
    http: HttpDownloader, httpserver: HTTPServer, sample: bytes, tmp_path: Path
) -> None:
    # e.g. a link that expired between the two requests
    httpserver.expect_request("/clip.mp4", method="HEAD").respond_with_handler(serve(sample))
    httpserver.expect_request("/clip.mp4", method="GET").respond_with_data("Gone", status=404)
    ctx = DownloadContext(url=httpserver.url_for("/clip.mp4"), output_dir=tmp_path)

    with pytest.raises(NotFound):
        await http.download(ctx)

    assert files_in(tmp_path) == []


async def test_an_empty_file_is_not_a_download(
    http: HttpDownloader, httpserver: HTTPServer, tmp_path: Path
) -> None:
    httpserver.expect_request("/clip.mp4").respond_with_handler(serve(b""))
    ctx = DownloadContext(url=httpserver.url_for("/clip.mp4"), output_dir=tmp_path)

    with pytest.raises(ExtractionFailed, match="empty"):
        await http.download(ctx)

    assert files_in(tmp_path) == []


async def test_a_file_of_another_size_than_the_server_said_is_not_kept(
    http: HttpDownloader, httpserver: HTTPServer, sample: bytes, tmp_path: Path
) -> None:
    def misreported(request: Request) -> Response:
        response = serve(sample)(request)
        if request.method == "HEAD":
            response.content_length = len(sample) + 100
        return response

    httpserver.expect_request("/clip.mp4").respond_with_handler(misreported)
    ctx = DownloadContext(url=httpserver.url_for("/clip.mp4"), output_dir=tmp_path)

    with pytest.raises(NetworkError, match=f"{len(sample)} bytes"):
        await http.download(ctx)

    assert not (tmp_path / "clip.mp4").exists()


async def test_a_compressed_answer_is_stored_uncompressed(
    http: HttpDownloader, httpserver: HTTPServer, tmp_path: Path
) -> None:
    # Asked for the file as it is (Accept-Encoding: identity), the server compresses it anyway.
    data = b"id,title\n" * 200

    def compressing(request: Request) -> Response:
        assert request.headers.get("Accept-Encoding") == "identity"
        headers = {"Content-Encoding": "gzip"}
        return Response(gzip.compress(data), mimetype="text/csv", headers=headers)

    httpserver.expect_request("/export.csv").respond_with_handler(compressing)
    ctx = DownloadContext(
        url=httpserver.url_for("/export.csv"),
        output_dir=tmp_path,
        headers={"accept-encoding": "gzip"},  # replaced
    )

    result = await http.download(ctx)

    assert result.files[0].read_bytes() == data


# ---------------------------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("disposition", "name"),
    [
        ('attachment; filename="clip.mp4"', "clip.mp4"),
        ("attachment; filename*=UTF-8''caf%C3%A9.mp4", "café.mp4"),
        ('inline; filename="../../escape.mp4"', "escape.mp4"),
    ],
)
async def test_the_file_is_named_as_the_server_says(
    http: HttpDownloader,
    httpserver: HTTPServer,
    sample: bytes,
    tmp_path: Path,
    disposition: str,
    name: str,
) -> None:
    httpserver.expect_request("/download").respond_with_handler(
        serve(sample, headers={"Content-Disposition": disposition})
    )
    ctx = DownloadContext(url=httpserver.url_for("/download?id=7"), output_dir=tmp_path)

    result = await http.download(ctx)

    assert result.files == [tmp_path / name]


async def test_a_name_without_an_extension_gets_one_from_its_type(
    http: HttpDownloader, httpserver: HTTPServer, sample: bytes, tmp_path: Path
) -> None:
    httpserver.expect_request("/download").respond_with_handler(serve(sample))
    ctx = DownloadContext(url=httpserver.url_for("/download"), output_dir=tmp_path)

    result = await http.download(ctx)

    assert result.files == [tmp_path / "download.mp4"]
    assert result.title == "download"


@pytest.mark.parametrize(
    ("template", "name"),
    [
        ("{title}.{ext}", "sample.mp4"),
        ("clips/{title}.{ext}", "clips/sample.mp4"),
        ("{title} [{id}].{ext}", "sample [NA].mp4"),  # a field http does not know
    ],
)
async def test_the_filename_template_names_the_file(
    http: HttpDownloader, fixture_site: FixtureSite, tmp_path: Path, template: str, name: str
) -> None:
    ctx = DownloadContext(
        url=fixture_site.url(Page.DIRECT_FILE), output_dir=tmp_path, filename_template=template
    )

    result = await http.download(ctx)

    assert result.files == [tmp_path / name]


# ---------------------------------------------------------------------------------------------
# Resuming
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def clip(httpserver: HTTPServer, sample: bytes) -> str:
    httpserver.expect_request("/clip.mp4").respond_with_handler(serve(sample))
    return httpserver.url_for("/clip.mp4")


async def test_a_partial_download_is_resumed(
    http: HttpDownloader, httpserver: HTTPServer, clip: str, sample: bytes, tmp_path: Path
) -> None:
    (tmp_path / "clip.mp4.part").write_bytes(sample[:100])

    result = await http.download(DownloadContext(url=clip, output_dir=tmp_path))

    assert result.files[0].read_bytes() == sample
    assert [r.headers.get("Range") for r in requests_to(httpserver, "/clip.mp4")] == ["bytes=100-"]
    assert not (tmp_path / "clip.mp4.part").exists()


async def test_a_part_bigger_than_the_file_is_started_again(
    http: HttpDownloader, clip: str, sample: bytes, tmp_path: Path
) -> None:
    (tmp_path / "clip.mp4.part").write_bytes(bytes(len(sample) + 100))

    result = await http.download(DownloadContext(url=clip, output_dir=tmp_path))

    assert result.files[0].read_bytes() == sample


async def test_a_server_that_cannot_resume_still_gives_the_whole_file(
    http: HttpDownloader, httpserver: HTTPServer, sample: bytes, tmp_path: Path
) -> None:
    # It sends the whole file again: httpx and curl start again, wget skips what it has.
    httpserver.expect_request("/clip.mp4").respond_with_handler(serve(sample, ranges=False))
    (tmp_path / "clip.mp4.part").write_bytes(sample[:100])
    ctx = DownloadContext(url=httpserver.url_for("/clip.mp4"), output_dir=tmp_path)

    result = await http.download(ctx)

    assert result.files[0].read_bytes() == sample


async def test_a_finished_part_is_not_downloaded_again(
    http: HttpDownloader, httpserver: HTTPServer, clip: str, sample: bytes, tmp_path: Path
) -> None:
    (tmp_path / "clip.mp4.part").write_bytes(sample)

    result = await http.download(DownloadContext(url=clip, output_dir=tmp_path))

    assert result.files[0].read_bytes() == sample
    assert requests_to(httpserver, "/clip.mp4") == []


async def test_a_file_downloaded_before_is_kept(
    http: HttpDownloader, httpserver: HTTPServer, clip: str, tmp_path: Path
) -> None:
    ctx = DownloadContext(url=clip, output_dir=tmp_path)
    first = await http.download(ctx)

    again = await http.download(ctx)

    assert again.files == first.files
    assert len(requests_to(httpserver, "/clip.mp4")) == 1


async def test_a_different_file_of_the_same_name_is_replaced(
    http: HttpDownloader, clip: str, sample: bytes, tmp_path: Path
) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"an older, shorter clip")

    result = await http.download(DownloadContext(url=clip, output_dir=tmp_path))

    assert result.files[0].read_bytes() == sample


async def test_a_download_cut_short_keeps_its_part_to_resume(
    backend: HttpBackend, httpserver: HTTPServer, sample: bytes, tmp_path: Path
) -> None:
    def cut_short(request: Request) -> Response:
        if request.method == "HEAD":
            return serve(sample)(request)
        response = Response(sample[:500], mimetype="video/mp4")
        response.content_length = len(sample)  # promises more than it sends
        return response

    httpserver.expect_request("/clip.mp4").respond_with_handler(cut_short)
    http = HttpDownloader(HttpOptions(backend=backend, retries=0))
    ctx = DownloadContext(url=httpserver.url_for("/clip.mp4"), output_dir=tmp_path)

    with pytest.raises(NetworkError):
        await http.download(ctx)

    assert (tmp_path / "clip.mp4.part").read_bytes() == sample[:500]
    assert not (tmp_path / "clip.mp4").exists()


async def test_a_cancelled_download_keeps_its_part_to_resume(
    http: HttpDownloader, fixture_site: FixtureSite, tmp_path: Path
) -> None:
    token = CancelToken()
    started = asyncio.Event()
    ctx = DownloadContext(
        url=fixture_site.url(Page.SLOW_FILE),
        output_dir=tmp_path,
        cancel=token,
        progress=lambda p: started.set() if p.downloaded_bytes else None,
    )
    task = asyncio.create_task(http.download(ctx))
    await asyncio.wait_for(started.wait(), timeout=10)

    token.cancel()

    with pytest.raises(Cancelled):
        await task
    assert (tmp_path / "slow.mp4.part").stat().st_size > 0


# ---------------------------------------------------------------------------------------------
# Headers and cookies
# ---------------------------------------------------------------------------------------------


async def test_headers_are_sent(
    http: HttpDownloader, httpserver: HTTPServer, sample: bytes, tmp_path: Path
) -> None:
    httpserver.expect_request("/clip.mp4", headers={"X-Udl-Test": "yes"}).respond_with_handler(
        serve(sample)
    )
    ctx = DownloadContext(
        url=httpserver.url_for("/clip.mp4"), output_dir=tmp_path, headers={"X-Udl-Test": "yes"}
    )

    result = await http.download(ctx)

    assert result.files[0].read_bytes() == sample


def user_agents(server: HTTPServer) -> set[tuple[str, ...]]:
    """The User-Agents each request to `server` sent."""
    return {tuple(request.headers.getlist("User-Agent")) for request, _ in server.log}


async def test_a_browser_s_user_agent_is_sent(
    http: HttpDownloader, httpserver: HTTPServer, sample: bytes, tmp_path: Path
) -> None:
    # Many sites refuse other User-Agents (a library's, curl's, wget's).
    httpserver.expect_request("/clip.mp4").respond_with_handler(serve(sample))

    await http.download(DownloadContext(url=httpserver.url_for("/clip.mp4"), output_dir=tmp_path))

    assert user_agents(httpserver) == {(USER_AGENT,)}
    assert USER_AGENT.startswith("Mozilla/5.0")


async def test_udl_s_http_client_sends_a_browser_s_user_agent_unless_told_another(
    httpserver: HTTPServer,
) -> None:
    # (For plugins' own requests, too.)
    httpserver.expect_request("/page").respond_with_data("ok")
    url = httpserver.url_for("/page")

    for headers in ({}, {"user-agent": "Mine/1"}):
        async with http_client(
            headers=headers, cookies=[], seconds=5, retries=0, tool="test"
        ) as client:
            await client.get(url)

    assert [r.headers.getlist("User-Agent") for r, _ in httpserver.log] == [
        [USER_AGENT],
        ["Mine/1"],
    ]


@pytest.mark.parametrize("name", ["User-Agent", "user-agent"])
async def test_a_download_s_own_user_agent_is_sent_instead(
    http: HttpDownloader, httpserver: HTTPServer, sample: bytes, tmp_path: Path, name: str
) -> None:
    # e.g. the browser's, handing off media it found
    httpserver.expect_request("/clip.mp4").respond_with_handler(serve(sample))
    url = httpserver.url_for("/clip.mp4")

    await http.download(DownloadContext(url=url, output_dir=tmp_path, headers={name: "Mine/1"}))

    assert user_agents(httpserver) == {("Mine/1",)}


async def test_the_user_agent_can_be_set(
    backend: HttpBackend, httpserver: HTTPServer, sample: bytes, tmp_path: Path
) -> None:
    http = HttpDownloader(HttpOptions(backend=backend, user_agent="Configured/2"))
    httpserver.expect_request("/clip.mp4").respond_with_handler(serve(sample))

    await http.download(DownloadContext(url=httpserver.url_for("/clip.mp4"), output_dir=tmp_path))

    assert user_agents(httpserver) == {("Configured/2",)}


@pytest.mark.parametrize("backend", [HttpBackend.CURL, HttpBackend.WGET])
async def test_headers_and_cookies_are_kept_off_the_command_line(
    backend: HttpBackend,
    httpserver: HTTPServer,
    sample: bytes,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Anyone on the machine can read a command line (`ps`), and a site plugin's header can be
    # a token.
    caplog.set_level(logging.DEBUG, logger="udl")
    httpserver.expect_request(
        "/clip.mp4", headers={"Authorization": "Bearer hunter2", "Cookie": "session=s3cret"}
    ).respond_with_handler(serve(sample))
    ctx = DownloadContext(
        url=httpserver.url_for("/clip.mp4"),
        output_dir=tmp_path,
        headers={"Authorization": "Bearer hunter2"},
        cookies=[Cookie(name="session", value=SecretStr("s3cret"), domain="localhost")],
    )

    result = await HttpDownloader(HttpOptions(backend=backend)).download(ctx)

    assert [file.read_bytes() for file in result.files] == [sample]
    commands = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Running")]
    assert commands
    assert not [c for c in commands if "hunter2" in c or "s3cret" in c]


@pytest.mark.parametrize("backend", [HttpBackend.CURL, HttpBackend.WGET])
async def test_a_header_with_a_line_break_is_refused(
    backend: HttpBackend, httpserver: HTTPServer, sample: bytes, tmp_path: Path
) -> None:
    # (It would be another header, or another line of wget's config.)
    httpserver.expect_request("/clip.mp4").respond_with_handler(serve(sample))
    ctx = DownloadContext(
        url=httpserver.url_for("/clip.mp4"), output_dir=tmp_path, headers={"X-A": "a\nexecute = b"}
    )

    with pytest.raises(DownloadError, match="'X-A' header has a line break"):
        await HttpDownloader(HttpOptions(backend=backend)).download(ctx)

    assert httpserver.log == []


async def test_cookies_are_sent_only_to_their_site(
    http: HttpDownloader, httpserver: HTTPServer, clip: str, tmp_path: Path
) -> None:
    cookies = [
        Cookie(name="mine", value=SecretStr("1"), domain="localhost"),
        Cookie(name="other", value=SecretStr("2"), domain="example.com"),
        Cookie(name="elsewhere", value=SecretStr("3"), domain="localhost", path="/private"),
    ]

    await http.download(DownloadContext(url=clip, output_dir=tmp_path, cookies=cookies))

    assert {request.headers.get("Cookie") for request, _ in httpserver.log} == {"mine=1"}
