"""
--------------
- browser.py -
--------------

udl browser login: sign in to a site in udl's browser profile, whose cookies udl's downloads and
fetches send; and udl browser cookies: import cookies into it (`import`, from a file or stdin, or
a site's from your own browser), list them (never their values) and delete a site's (the last
two made from their operations)
"""

# System Imports
import asyncio
import contextlib
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated

# Package Imports
import typer

# Local Imports
from udl.core.urls import URL
from udl.fetch.models import ImportCookiesRequest
from udl.interfaces.cli import render
from udl.interfaces.cli.common import (
    JsonOption,
    Runtime,
    parse,
    parse_url,
    run,
    run_async,
    runs,
    show,
    stderr,
    stdout,
    usage_error,
)
from udl.plugins.downloaders.auth import browser_login
from udl.plugins.downloaders.base import Cookie

app = typer.Typer(
    help="udl's browser profile: where you sign in to sites for udl, and its cookies.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)
cookies = typer.Typer(
    help="The cookies in udl's browser profile, which its fetches and downloads send.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)
app.add_typer(cookies, name="cookies")


@contextlib.asynccontextmanager
async def enter_pressed() -> AsyncGenerator[asyncio.Future[None] | None]:
    """A future done once Enter is pressed on stdin, while in the context, when stdin is a
    terminal (else None)."""
    try:
        fd = sys.stdin.fileno() if sys.stdin.isatty() else None
    except AttributeError, OSError, ValueError:  # (replaced, closed or not a real file)
        fd = None
    if fd is None:
        yield None
        return
    loop = asyncio.get_running_loop()
    pressed = loop.create_future()

    def read() -> None:
        loop.remove_reader(fd)
        sys.stdin.readline()
        pressed.set_result(None)

    loop.add_reader(fd, read)
    try:
        yield pressed
    finally:
        loop.remove_reader(fd)
        pressed.cancel()


async def login_on(page: URL, profile_dir: Path) -> list[Cookie]:
    """Open `page` in a browser on the profile in `profile_dir`, until the user closes it (or
    presses Enter, on a terminal). Returns the cookies the profile has for the page's site."""
    async with enter_pressed() as pressed:
        return await browser_login(page.value, profile_dir=profile_dir, until=pressed)


@app.command("login")
def login(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="A page of the site, e.g. its login page.")],
) -> None:
    """Sign in to a site in a browser on udl's profile, for udl's downloads from it.

    udl's downloads from the site then send its cookies. Press Enter here (or close the browser)
    when you have signed in."""
    page = parse_url(url)
    profile_dir = Runtime.of(ctx).settings.browser.profile_dir
    stderr().print(
        f"Sign in to {page.site_key} in the browser, then press Enter here (or close the browser)."
    )
    cookies = run_async(login_on(page, profile_dir))
    stdout().print(f"udl's browser profile has {len(cookies)} cookies for {page.site_key}.")


@cookies.command("import")
@runs("import_cookies")
def import_cookies(
    ctx: typer.Context,
    file: Annotated[
        str | None,
        typer.Argument(
            help="A cookies.txt, a JSON export (a browser extension's, or Playwright's storage "
            "state) or a Cookie header, in a file (- for stdin).",
            show_default=False,
        ),
    ] = None,
    site: Annotated[
        str | None,
        typer.Option(help="Only this site's cookies (a Cookie header and --from-browser need it)."),
    ] = None,
    from_browser: Annotated[
        str | None,
        typer.Option(
            help="Copy the site's cookies from your browser instead (e.g. firefox, or "
            "'chrome:Profile 1').",
            show_default=False,
        ),
    ] = None,
    user_agent: Annotated[
        str | None,
        typer.Option(
            help="The User-Agent of the browser they came from: udl's plain requests "
            "send it with them.",
            show_default=False,
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Import cookies (a login, a check's clearance) into udl's browser profile, for its
    fetches and downloads. Their values are never shown."""
    text: str | None = None
    if file is not None:
        try:
            text = sys.stdin.read() if file == "-" else Path(file).read_text()
        except (OSError, UnicodeDecodeError) as e:
            usage_error(f"Cannot read the cookies in {'stdin' if file == '-' else file}: {e}")
    elif from_browser is None:
        usage_error("Name a file of cookies (- for stdin), or --from-browser with --site.")
    request = parse(
        ImportCookiesRequest,
        cookies=text,
        site=site,
        from_browser=from_browser,
        user_agent=user_agent,
    )
    show(run(ctx, lambda service: service.import_cookies(request)), as_json, render.cookie_import)
