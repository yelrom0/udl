"""
-----------------
- test_fetch.py -
-----------------

udl fetch (`udl fetch <url>...` and its commands) and udl browser cookies, against the anti-bot
fixture site's pages that a plain request reads
"""

# System Imports
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.antibot import SESSION, AntiBotSite
from support.browser import needs_chromium
from support.cli import CLI
from support.interfaces import Harness
from udl.config import FetchSettings

FAST = FetchSettings(
    site_delay=timedelta(0), timeout=timedelta(seconds=10)
)  # (no browser is needed for these pages)


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path, fetch=FAST)


def fetched_id(cli: CLI, *urls: str) -> dict[str, Any]:
    result = cli("fetch", *urls, "--json")
    assert result.exit_code in (0, 1), result.output
    return json.loads(result.stdout)


def test_udl_fetch_prints_a_pages_main_content(cli: CLI, antibot: AntiBotSite) -> None:
    result = cli("fetch", antibot.url("/docs"))

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("# Widgets: a guide")
    assert "[gears](http://a.localhost:" in result.stdout
    assert "Blog" not in result.stdout


def test_udl_fetch_get_is_the_same_command(cli: CLI, antibot: AntiBotSite) -> None:
    result = cli("fetch", "get", antibot.url("/docs"), "--format", "text", "--select", "h2")

    assert result.exit_code == 0, result.output
    assert result.stdout == "Installing\n\nSizes\n"


def test_its_options_come_before_or_after_the_urls(cli: CLI, antibot: AntiBotSite) -> None:
    result = cli("fetch", "--whole", antibot.url("/docs"))

    assert result.exit_code == 0, result.output
    assert "Blog" in result.stdout


def test_its_progress_goes_to_stderr(cli: CLI, antibot: AntiBotSite) -> None:
    result = cli("fetch", antibot.url("/docs"))

    assert "succeeded" in result.stderr
    assert antibot.url("/docs") in result.stderr


def test_several_pages_are_listed(cli: CLI, antibot: AntiBotSite) -> None:
    result = cli("fetch", antibot.url("/docs"), antibot.url("/article", "b.localhost"))

    assert result.exit_code == 0, result.output
    for text in ("finished", "2 succeeded", "Widgets: a guide", "The river in winter", "static"):
        assert text in result.stdout


def test_a_page_that_is_not_read_exits_with_1(cli: CLI, antibot: AntiBotSite) -> None:
    result = cli("fetch", antibot.url("/docs"), antibot.url("/gone"))

    assert result.exit_code == 1
    assert "not_found" in result.stdout


def test_out_writes_each_page_to_its_sites_folder(
    cli: CLI, antibot: AntiBotSite, tmp_path: Path
) -> None:
    out = tmp_path / "pages"

    result = cli(
        "fetch", antibot.url("/docs"), antibot.url("/article", "b.localhost"), "--out", out
    )

    assert result.exit_code == 0, result.output
    assert (out / "a.localhost" / "docs.md").read_text().startswith("# Widgets: a guide")
    assert (out / "b.localhost" / "article.md").read_text().startswith("# The river in winter")
    assert "Wrote" in result.stderr


def test_out_with_raw_keeps_the_bytes_as_served(
    cli: CLI, antibot: AntiBotSite, tmp_path: Path
) -> None:
    out = tmp_path / "pages"

    result = cli("fetch", antibot.url("/report.pdf"), antibot.url("/docs"), "-F", "raw", "-o", out)

    assert result.exit_code == 0, result.output
    assert (out / "a.localhost" / "report.pdf").read_bytes() == antibot.content("/report.pdf")
    assert (out / "a.localhost" / "docs.html").read_bytes().startswith(b"<!doctype html>")


def test_out_does_not_overwrite_a_page_of_the_same_name(
    cli: CLI, antibot: AntiBotSite, tmp_path: Path
) -> None:
    out = tmp_path / "pages"
    cli("fetch", antibot.url("/docs"), "-o", out)

    cli("fetch", antibot.url("/docs"), "-o", out)

    assert sorted(path.name for path in (out / "a.localhost").iterdir()) == ["docs-2.md", "docs.md"]


def test_one_raw_page_goes_to_stdout_as_it_was_served(cli: CLI, antibot: AntiBotSite) -> None:
    result = cli("fetch", antibot.url("/data.json"), "--format", "raw")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"name": "widget", "sizes": [12, 24, 48], "in_stock": True}


def test_json_gives_the_fetch(cli: CLI, antibot: AntiBotSite) -> None:
    shown = fetched_id(cli, antibot.url("/docs"))

    assert shown["status"] == "finished"
    [item] = shown["items"]
    assert item["via"] == "static"


def test_secret_headers_are_refused(cli: CLI, antibot: AntiBotSite) -> None:
    result = cli("fetch", antibot.url("/docs"), "-H", "Cookie: a=1")

    assert result.exit_code == 2
    assert "import_cookies" in result.output


def test_a_header_that_is_not_one_is_refused(cli: CLI, antibot: AntiBotSite) -> None:
    result = cli("fetch", antibot.url("/docs"), "-H", "no colon")

    assert result.exit_code == 2


def test_no_urls_is_a_usage_error(cli: CLI) -> None:
    assert cli("fetch", "get").exit_code == 2


def test_urls_can_come_from_a_file(cli: CLI, antibot: AntiBotSite, tmp_path: Path) -> None:
    listed = tmp_path / "urls.txt"
    listed.write_text(f"# pages\n{antibot.url('/docs')}\n\n")

    result = cli("fetch", "-i", listed)

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("# Widgets")


# ---------------------------------------------------------------------------------------------
# The fetch group's commands
# ---------------------------------------------------------------------------------------------


def test_fetch_list_and_show(cli: CLI, antibot: AntiBotSite) -> None:
    shown = fetched_id(cli, antibot.url("/docs"))

    listed = cli("fetch", "list")
    one = cli("fetch", "show", str(shown["id"]))

    assert listed.exit_code == 0, listed.output
    assert str(shown["id"]) in listed.stdout
    assert "1 succeeded" in listed.stdout
    assert one.exit_code == 0, one.output
    assert "Widgets: a guide" in one.stdout


def test_fetch_list_says_when_there_are_none(cli: CLI) -> None:
    assert "No fetches" in cli("fetch", "list").stdout


def test_fetch_page_gives_a_page_in_any_format(cli: CLI, antibot: AntiBotSite) -> None:
    shown = fetched_id(cli, antibot.url("/docs"))
    item = shown["items"][0]["id"]

    as_html = cli("fetch", "page", str(item), "-F", "html", "--max-chars", "15")
    from_offset = cli("fetch", "page", str(item), "--offset", "2", "--max-chars", "7")

    assert as_html.exit_code == 0, as_html.output
    assert as_html.stdout.startswith("<!doctype html>")
    assert "--offset 15" in as_html.stdout
    assert from_offset.stdout.startswith("Widgets")


def test_fetch_page_of_a_url_that_failed_says_why(cli: CLI, antibot: AntiBotSite) -> None:
    shown = fetched_id(cli, antibot.url("/gone"))
    item = shown["items"][0]["id"]

    result = cli("fetch", "page", str(item))

    assert result.exit_code == 0, result.output
    assert "not_found" in result.stdout


def test_fetch_cancel_leaves_a_fetch_that_ended(cli: CLI, antibot: AntiBotSite) -> None:
    shown = fetched_id(cli, antibot.url("/docs"))

    result = cli("fetch", "cancel", str(shown["id"]))

    assert result.exit_code == 0, result.output
    assert "finished" in result.stdout


def test_fetch_retry_tries_what_failed_again(cli: CLI, antibot: AntiBotSite) -> None:
    antibot.flaky = 6  # (four tries fail the first fetch, then it is read)
    shown = fetched_id(cli, antibot.url("/flaky/x"))

    result = cli("fetch", "retry", str(shown["id"]), "--via", "static")

    assert shown["status"] == "finished"
    assert result.exit_code == 0, result.output
    assert "queued" in result.stdout or "running" in result.stdout


def test_fetch_challenges_and_solve_when_nothing_waits(cli: CLI) -> None:
    listed = cli("fetch", "challenges")
    solved = cli("fetch", "solve")

    assert "No checks wait for you" in listed.stdout
    assert solved.exit_code == 0, solved.output
    assert "No checks wait for you" in solved.stdout


# ---------------------------------------------------------------------------------------------
# udl browser cookies
# ---------------------------------------------------------------------------------------------


def test_imported_cookies_let_udl_fetch_read_a_members_page(
    cli: CLI, antibot: AntiBotSite, tmp_path: Path
) -> None:
    needs_chromium()
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        f"a.localhost\tFALSE\t/\tFALSE\t0\t{SESSION}\t{antibot.issue_session()}\n"
    )

    imported = cli("browser", "cookies", "import", cookies)
    result = cli("fetch", antibot.url("/members/home"))

    assert imported.exit_code == 0, imported.output
    assert "1 cookie of a.localhost" in imported.stdout
    assert result.exit_code == 0, result.output
    assert "For members only: home." in result.stdout


def test_cookies_are_imported_from_stdin_listed_without_values_and_removed(
    cli: CLI, antibot: AntiBotSite
) -> None:
    needs_chromium()
    secret = antibot.issue_session()

    imported = cli(
        "browser",
        "cookies",
        "import",
        "-",
        "--site",
        "b.localhost",
        "--user-agent",
        "UA/1",
        stdin=f"{SESSION}={secret}",
    )
    listed = cli("browser", "cookies", "list")
    removed = cli("browser", "cookies", "rm", "b.localhost")
    after = cli("browser", "cookies", "list", "--json")

    assert imported.exit_code == 0, imported.output
    assert listed.exit_code == 0, listed.output
    for text in ("b.localhost", SESSION, "session", "UA/1"):
        assert text in listed.stdout
    assert secret not in listed.output
    assert removed.stdout.strip() == "Deleted."
    assert json.loads(after.stdout) == {"sites": []}


def test_importing_needs_cookies_or_a_browser(cli: CLI) -> None:
    result = cli("browser", "cookies", "import")

    assert result.exit_code == 2
    assert "--from-browser" in result.output


def test_a_file_of_cookies_that_cannot_be_read_is_a_usage_error(cli: CLI, tmp_path: Path) -> None:
    result = cli("browser", "cookies", "import", tmp_path / "missing.txt")

    assert result.exit_code == 2
    assert "Cannot read the cookies" in result.output


def test_cookies_that_are_not_cookies_are_refused(cli: CLI, tmp_path: Path) -> None:
    junk = tmp_path / "junk.txt"
    junk.write_text("just some words")

    result = cli("browser", "cookies", "import", junk)

    assert result.exit_code == 2
    assert "cookies.txt" in result.output
