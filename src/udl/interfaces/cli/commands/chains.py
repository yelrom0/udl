"""
-------------
- chains.py -
-------------

udl chain suggest / test / set / import / export: tag chains, for sites no downloader handles on
its own. And udl chain wizard, which suggests chains, lets you pick one (or write your own),
tests it and saves it.
"""

# System Imports
import sys
from pathlib import Path
from typing import Annotated

# Package Imports
import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape

# Local Imports
from udl.chains.models import FetchMode, TagChain
from udl.config import config_dir
from udl.core.models import (
    ChainFile,
    ChainSuggestions,
    ChainTestRequest,
    ImportChainsRequest,
    ImportedChains,
    SetChainRequest,
    SiteRequest,
    SuggestChainRequest,
)
from udl.core.service import UDLService
from udl.db.models import Strategy
from udl.errors import ConfigError
from udl.interfaces.cli import render
from udl.interfaces.cli.common import (
    EXIT_FAILED,
    JsonOption,
    parse,
    parse_url,
    problems,
    run,
    runs,
    show,
    show_error,
    stderr,
    stdout,
    usage_error,
)

app = typer.Typer(
    help="Tag chains: the steps from a page to its media, for sites no downloader handles on its "
    "own. `udl chain wizard <url>` finds one with you.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)

Url = Annotated[str, typer.Argument(help="A page of the site.")]
Site = Annotated[str, typer.Argument(help="The site: its key (example.com), a host or a URL.")]
Chain = Annotated[
    str,
    typer.Argument(
        help="The chain in its one-line shorthand, e.g. 'iframe#player@src >> video source@src'.",
        metavar="CHAIN",
    ),
]
Fetch = Annotated[
    FetchMode | None,
    typer.Option(
        help="How to read the page: static (as served) or browser (rendered). By default "
        "static, then the browser when that finds nothing."
    ),
]
Browser = Annotated[
    bool,
    typer.Option(
        "--browser",
        help="Follow it in the browser (one with click, wait_for or network steps always is).",
    ),
]
Handoff = Annotated[
    str | None,
    typer.Option(
        help="The downloader that downloads the media (by default http for a file, yt-dlp for "
        "a stream).",
        metavar="TOOL",
    ),
]
Headers = Annotated[
    list[str] | None,
    typer.Option(
        "-H",
        "--header",
        help="A header to send with every request, e.g. -H 'Referer: {url}' ({url} is the "
        "page's URL).",
        metavar="'NAME: VALUE'",
    ),
]
Pattern = Annotated[
    str | None,
    typer.Option(
        help="Follow it only for URLs whose path this regex is found in, e.g. '^/watch/'.",
        metavar="REGEX",
    ),
]


def _headers(pairs: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for pair in pairs:
        name, colon, value = pair.partition(":")
        if not name.strip() or not colon:
            raise typer.BadParameter(
                f"{pair!r} is not 'NAME: VALUE'", param_hint="'-H' / '--header'"
            )
        headers[name.strip()] = value.strip()
    return headers


def _chain(
    steps: str, *, browser: bool, handoff: str | None, headers: list[str] | None
) -> dict[str, object]:
    """A chain as the command's arguments give it (its request validates it)."""
    chain: dict[str, object] = {
        "steps": steps,
        "headers": _headers(headers or []),
        "handoff": handoff,
    }
    if browser:
        chain["fetch"] = FetchMode.BROWSER
    return chain


@app.command("suggest")
@runs("suggest_chain")
def suggest(
    ctx: typer.Context,
    url: Url,
    fetch: Fetch = None,
    limit: Annotated[
        int,
        typer.Option("-n", "--limit", min=1, max=50, help="Suggest at most N.", metavar="N"),
    ] = 10,
    as_json: JsonOption = False,
) -> None:
    """Suggest tag chains for the media on a page, best first.

    Try one with `udl chain test`, then save it with `udl chain set` (`udl chain wizard` does it
    all with you). Exits with 1 when there are none."""
    request = parse(SuggestChainRequest, url=url, fetch=fetch, limit=limit)
    found = run(ctx, lambda service: service.suggest_chain(request))
    show(found, as_json, render.suggestions)
    if not found.suggestions:
        raise typer.Exit(EXIT_FAILED)


@app.command("test")
@runs("test_chain")
def try_chain(
    ctx: typer.Context,
    url: Url,
    chain: Chain,
    browser: Browser = False,
    handoff: Handoff = None,
    header: Headers = None,
    as_json: JsonOption = False,
) -> None:
    """Try a tag chain on a page, downloading nothing.

    Shows what each step found, and the media the chain leads to. Steps are separated by >>: a
    CSS selector then @attr or ::text (a selector after a URL opens it first), regex:PATTERN,
    json:PATH, follow, and in the browser click:SELECTOR, wait_for:SELECTOR and network:PATTERN;
    `| all` or `| best` keeps all (or the best) of what a step finds. Exits with 1 when the chain
    leads to no media."""
    request = parse(
        ChainTestRequest,
        url=url,
        chain=_chain(chain, browser=browser, handoff=handoff, headers=header),
    )
    report = run(ctx, lambda service: service.test_chain(request))
    show(report, as_json, render.chain_report)
    if not report.ok:
        raise typer.Exit(EXIT_FAILED)


@app.command("set")
@runs("set_site_chain")
def set_chain(
    ctx: typer.Context,
    site: Site,
    chain: Chain,
    pattern: Pattern = None,
    browser: Browser = False,
    handoff: Handoff = None,
    header: Headers = None,
    priority: Annotated[int, typer.Option(help="Lower is tried first.")] = 0,
    requires_auth: Annotated[
        bool, typer.Option("--requires-auth", help="Send the site's cookies from the first try.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Save a tag chain for a site (or for some of its URLs): its downloads then follow it.

    udl tries it before what it has learned, and never replaces it when it fails. Saving a chain
    for the same pattern again replaces it."""
    request = parse(
        SetChainRequest,
        site=site,
        chain=_chain(chain, browser=browser, handoff=handoff, headers=header),
        pattern=pattern,
        priority=priority,
        requires_auth=requires_auth,
    )
    show(run(ctx, lambda service: service.set_site_chain(request)), as_json, render.chain_strategy)


def sites_dir() -> Path:
    """Where chain files are kept: `udl chain import` reads them, `udl chain wizard` writes
    them."""
    return config_dir() / "sites"


def _chain_files(files: list[Path]) -> list[tuple[str, str]]:
    """The files' names and texts (- is stdin); by default, every chain file in sites_dir()."""
    if not files:
        folder = sites_dir()
        files = sorted([*folder.glob("*.yaml"), *folder.glob("*.yml")])
        if not files:
            usage_error(f"There are no chain files in {folder}: name some (- for stdin).")
    texts: list[tuple[str, str]] = []
    for file in files:
        if str(file) == "-":
            texts.append(("stdin", sys.stdin.read()))
            continue
        try:
            texts.append((str(file), file.read_text()))
        except OSError as e:
            usage_error(f"Could not read {file}: {e.strerror or e}")
    return texts


@app.command("import")
@runs("import_chains")
def import_chains(
    ctx: typer.Context,
    files: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Chain files (YAML, as `udl chain export` writes them; - for stdin). By "
            "default every *.yaml file in ~/.config/udl/sites.",
            show_default=False,
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Save the tag chains in chain files, each for its site.

    The files are imported in turn; one with a problem stops the import (the files before it
    stay imported)."""
    texts = _chain_files(files or [])

    async def import_all(service: UDLService) -> ImportedChains:
        strategies: list[Strategy] = []
        for name, text in texts:
            try:
                imported = await service.import_chains(ImportChainsRequest(yaml=text))
            except ConfigError as e:
                raise ConfigError(f"{name}: {e.message}", hint=e.hint) from e
            strategies += imported.strategies
        return ImportedChains(strategies=strategies)

    show(run(ctx, import_all), as_json, render.imported)


def _write(path: Path, exported: ChainFile) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(exported.yaml)
    except OSError as e:
        show_error(stderr(), f"Could not write {path}: {e.strerror or e}")
        raise typer.Exit(EXIT_FAILED) from None


@app.command("export")
@runs("export_chain")
def export_chain(
    ctx: typer.Context,
    site: Site,
    output: Annotated[
        Path | None,
        typer.Option(
            "-o", "--output", help="Write the chain file here (else it is printed).", metavar="FILE"
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Give a site's tag chains as a chain file (YAML), which `udl chain import` reads.

    Exits with 1 when the site has none."""
    request = parse(SiteRequest, site=site)
    exported = run(ctx, lambda service: service.export_chain(request))
    if output is None and not as_json:
        typer.echo(exported.yaml, nl=False)
        return
    if output is not None:
        _write(output, exported)

    def wrote(console: Console, shown: ChainFile) -> None:
        count = len(shown.chains)
        chains = "chain" if count == 1 else "chains"
        console.print(f"Wrote {count} {chains} for {shown.site} to {output}.")

    show(exported, as_json, wrote)


# ---------------------------------------------------------------------------------------------
# The wizard
# ---------------------------------------------------------------------------------------------


def _pick(found: ChainSuggestions) -> TagChain:
    """The chain the user picks from the suggestions, or writes."""
    count = len(found.suggestions)
    question = f"Pick a chain (1-{count}), or write one" if count else "Write a chain"
    while True:
        answer = typer.prompt(question).strip()
        if answer.isdigit() and 1 <= int(answer) <= count:
            return found.suggestions[int(answer) - 1].chain
        written: dict[str, object] = {"steps": answer}
        if found.fetch is FetchMode.BROWSER:  # (the page needed the browser)
            written["fetch"] = FetchMode.BROWSER
        try:
            return TagChain.model_validate(written)
        except ValidationError as e:
            show_error(stderr(), problems(e), "See `udl chain test --help` for how to write one.")


@app.command("wizard")
def wizard(ctx: typer.Context, url: Url, pattern: Pattern = None, fetch: Fetch = None) -> None:
    """Find a tag chain for a site with udl, from one of its pages.

    udl suggests chains to the media on the page; you pick one (or write your own), udl tests
    it, and saves it for the site. It can also write the site's chains to a chain file in
    ~/.config/udl/sites, which `udl chain import` reads (e.g. on another machine)."""
    page = parse_url(url)
    console = stdout()
    stderr().print(f"Looking for media on {escape(page.value)}…")
    found = run(
        ctx, lambda service: service.suggest_chain(SuggestChainRequest(url=page.value, fetch=fetch))
    )
    render.suggestions(console, found)
    while True:
        chain = _pick(found)
        tried = ChainTestRequest(url=page.value, chain=chain)
        report = run(ctx, lambda service: service.test_chain(tried))  # noqa: B023 - (run at once)
        render.chain_report(console, report)
        if not report.ok:
            console.print("Pick another chain, or write one.")
        elif typer.confirm(f"Save it for {page.site_key}?", default=True):
            break
    request = SetChainRequest(site=page.site_key, chain=chain, pattern=pattern)
    render.chain_strategy(console, run(ctx, lambda service: service.set_site_chain(request)))
    path = sites_dir() / f"{page.site_key}.yaml"
    if typer.confirm(
        f"Also write {page.site_key}'s chains to {path} (`udl chain import` reads it)?",
        default=False,
    ):
        exported = run(ctx, lambda service: service.export_chain(SiteRequest(site=page.site_key)))
        _write(path, exported)
        console.print(f"Wrote {path}.")
