"""
--------------
- browser.py -
--------------

udl browser login: sign in to a site in udl's browser profile, whose cookies udl's downloads send
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
from udl.interfaces.cli.common import Runtime, parse_url, run_async, stderr, stdout
from udl.plugins.downloaders.auth import browser_login
from udl.plugins.downloaders.base import Cookie

app = typer.Typer(
    help="udl's browser profile: where you sign in to sites for udl.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)


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
