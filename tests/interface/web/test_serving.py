"""
------------------
- test_serving.py -
------------------

The web UI at /ui (udl.interfaces.web.app): the shell's build, with the headers and the caching
that make it safe and fast, each UI plugin's bundle at /ui/plugins/<name>/, and / redirecting a
browser to it. Without a build, /ui/ says to run `make web`.
"""

# System Imports
import shutil
import subprocess
import zipfile
from pathlib import Path

# Package Imports
import httpx
import pytest
from pydantic import SecretStr

# Local Imports
from support.api import client_for
from support.interfaces import Harness
from udl.config import APISettings, WebSettings
from udl.interfaces.api.app import create_app

REPO = Path(__file__).resolve().parents[3]
DIST = REPO / "src" / "udl" / "interfaces" / "web" / "dist"

INDEX = "<!doctype html><html><body><div id='root'></div></body></html>"

# Every /ui response carries these: no script or connection but udl's own, no framing, no
# sniffing, and no referrer to another site.
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; connect-src 'self'; frame-ancestors 'none'",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


def built(tmp_path: Path) -> Path:
    """A dist as `make web` leaves it: an index, and hashed assets."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "main-abc123.js").write_text("console.log('udl')\n")
    return dist


def plugins(tmp_path: Path) -> Path:
    """A `ui_plugin_dirs` folder with one UI plugin's bundle in it."""
    folder = tmp_path / "ui-plugins"
    bundle = folder / "gmail"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        '{"name": "gmail", "version": "0.1.0", "entry": "index.js"}'
    )
    (bundle / "index.js").write_text("export default {}\n")
    (bundle / "index.css").write_text(".gmail { color: red }\n")
    return folder


def assert_ui_headers(answer: httpx.Response) -> None:
    for name, value in HEADERS.items():
        assert answer.headers[name] == value


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """A udl whose UI is built, with one UI plugin's bundle."""
    dist = built(tmp_path)
    monkeypatch.setattr("udl.interfaces.web.app.DIST", dist)
    return Harness(
        tmp_path,
        web=WebSettings(enabled=True, ui_plugin_dirs=[plugins(tmp_path)]),
        api=APISettings(allowed_output_roots=[tmp_path]),
    )


async def test_the_shell_is_served_at_ui(served: Harness) -> None:
    async with client_for(served) as client:
        answer = await client.get("/ui/")

    assert answer.status_code == 200
    assert answer.headers["Content-Type"].startswith("text/html")
    assert answer.text == INDEX
    # (It is not hashed, so a new build is seen on the next load.)
    assert answer.headers["Cache-Control"] == "no-cache"
    assert_ui_headers(answer)


async def test_a_client_route_answers_the_shell(served: Harness) -> None:
    # (The router's routes are the browser's, so the server answers index.html for them.)
    async with client_for(served) as client:
        answer = await client.get("/ui/downloads/abc", headers={"Accept": "text/html"})

    assert answer.status_code == 200
    assert answer.text == INDEX
    assert answer.headers["Cache-Control"] == "no-cache"
    assert_ui_headers(answer)


async def test_an_asset_is_cached_forever(served: Harness) -> None:
    async with client_for(served) as client:
        answer = await client.get("/ui/assets/main-abc123.js")

    assert answer.status_code == 200
    assert answer.text == "console.log('udl')\n"
    assert answer.headers["Content-Type"].startswith("text/javascript")
    # (Vite puts a hash of the content in the name, so the file never changes.)
    assert answer.headers["Cache-Control"] == "max-age=31536000, immutable"
    assert_ui_headers(answer)


async def test_a_missing_asset_is_not_the_shell(served: Harness) -> None:
    # (A script the build did not make is a 404, not index.html: a browser would parse the page
    # as a module, and say something confusing instead of "this build is stale".)
    async with client_for(served) as client:
        answer = await client.get("/ui/assets/gone-000.js")

    assert answer.status_code == 404


async def test_a_ui_plugins_bundle_is_served(served: Harness) -> None:
    async with client_for(served) as client:
        script = await client.get("/ui/plugins/gmail/index.js")
        style = await client.get("/ui/plugins/gmail/index.css")

    assert script.status_code == 200
    assert script.text == "export default {}\n"
    assert script.headers["Content-Type"].startswith("text/javascript")
    assert style.headers["Content-Type"].startswith("text/css")
    for answer in (script, style):
        # (A plugin's bundle is not hashed: the shell asks again on each load, and an unchanged
        # file is a 304.)
        assert answer.headers["Cache-Control"] == "no-cache"
        assert answer.headers["ETag"]
        assert_ui_headers(answer)


async def test_a_ui_plugins_bundle_is_not_sent_again_when_it_has_not_changed(
    served: Harness,
) -> None:
    async with client_for(served) as client:
        first = await client.get("/ui/plugins/gmail/index.js")
        etag = first.headers["ETag"]
        again = await client.get("/ui/plugins/gmail/index.js", headers={"If-None-Match": etag})

    assert again.status_code == 304
    assert again.content == b""
    assert_ui_headers(again)


async def test_a_ui_plugin_that_is_not_there_is_a_404(served: Harness) -> None:
    async with client_for(served) as client:
        plugin = await client.get("/ui/plugins/slack/index.js")
        file = await client.get("/ui/plugins/gmail/gone.js")
        # (A folder without a manifest.json is not a UI plugin.)
        not_a_plugin = await client.get("/ui/plugins/gmail/../../../etc/passwd")

    for answer in (plugin, file, not_a_plugin):
        assert answer.status_code == 404
        assert "passwd" not in answer.text


async def test_a_plugin_path_cannot_lead_outside_its_bundle(
    tmp_path: Path, served: Harness
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("not for a page")
    # (A symlink in a bundle is a real way out; so is a `..` in the path.)
    (tmp_path / "ui-plugins" / "gmail" / "link.js").symlink_to(secret)

    async with client_for(served) as client:
        linked = await client.get("/ui/plugins/gmail/link.js")
        dotted = await client.get("/ui/plugins/gmail/%2E%2E/%2E%2E/secret.txt")

    for answer in (linked, dotted):
        assert answer.status_code == 404
        assert "not for a page" not in answer.text


async def test_a_browser_is_sent_to_the_shell(served: Harness) -> None:
    async with client_for(served) as client:
        answer = await client.get("/", headers={"Accept": "text/html,application/xhtml+xml"})

    assert answer.status_code == 307
    assert answer.headers["Location"] == "/ui/"


async def test_a_script_is_not_sent_to_the_shell(served: Harness) -> None:
    # (Only a browser's page is; a script that gets a page instead of JSON would be confusing.)
    async with client_for(served) as client:
        answer = await client.get("/", headers={"Accept": "application/json"})

    assert answer.status_code == 404


async def test_without_a_build_the_shell_says_to_build_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("udl.interfaces.web.app.DIST", tmp_path / "not-built")
    harness = Harness(tmp_path, api=APISettings(allowed_output_roots=[tmp_path]))

    async with client_for(harness) as client:
        shell = await client.get("/ui/")
        client_route = await client.get("/ui/downloads")
        asset = await client.get("/ui/assets/main-abc123.js")

    for answer in (shell, client_route):
        assert answer.status_code == 200
        assert "make web" in answer.text
        assert answer.headers["Cache-Control"] == "no-cache"
        assert_ui_headers(answer)
    assert asset.status_code == 404


async def test_the_ui_needs_no_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # (The files hold no data; the API's answers still need the key.)
    monkeypatch.setattr("udl.interfaces.web.app.DIST", built(tmp_path))
    harness = Harness(tmp_path, api=APISettings(api_key=SecretStr("s3cret")))

    async with client_for(harness) as client:
        shell = await client.get("/ui/")
        asset = await client.get("/ui/assets/main-abc123.js")
        sites = await client.get("/sites")

    assert shell.status_code == 200
    assert asset.status_code == 200
    assert sites.status_code == 401


async def test_no_web_ui_when_it_is_disabled(served: Harness) -> None:
    served.settings = served.settings.model_copy(update={"web": WebSettings(enabled=False)})

    async with client_for(served) as client:
        shell = await client.get("/ui/")
        root = await client.get("/", headers={"Accept": "text/html"})

    assert shell.status_code == 404
    assert root.status_code == 404


def test_the_ui_is_not_in_the_schema(served: Harness) -> None:
    # (The schema is the API's, which a client of udl's is made from: the UI's routes are not
    # operations, and they have no API key.)
    paths = create_app(served.settings, make_service=served.service).openapi()["paths"]

    for name in ("/", "/ui/", "/ui/assets/{path:path}", "/ui/{path:path}"):
        assert name not in paths


@pytest.mark.skipif(not (DIST / "index.html").is_file(), reason="the UI is not built (make web)")
def test_the_wheel_holds_the_built_ui(tmp_path: Path) -> None:
    # (The build is package data: an installed udl serves the shell it was built with. It is not
    # committed, so `uv build` only has it after `make web` — CI's e2e job runs this then.)
    uv = shutil.which("uv")
    assert uv is not None, "uv is how the package is built"

    subprocess.run(  # noqa: S603 - uv, building this repo's own wheel
        [uv, "build", "--wheel", "--out-dir", str(tmp_path)], cwd=REPO, check=True
    )

    (wheel,) = tmp_path.glob("udl-*.whl")
    with zipfile.ZipFile(wheel) as zipped:
        names = zipped.namelist()

    assert "udl/interfaces/web/dist/index.html" in names
    assert any(name.startswith("udl/interfaces/web/dist/assets/") for name in names)
