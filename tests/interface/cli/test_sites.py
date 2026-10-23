"""
-----------------
- test_sites.py -
-----------------

udl sites list / show / set / rm
"""

# Local Imports
from support.cli import CLI
from support.interfaces import Harness
from udl.core.models import Deleted, SiteInfo, SiteList
from udl.db.manager import DBManager
from udl.db.models import Site, Strategy, StrategySource


def site_info(cli: CLI, site: str) -> SiteInfo:
    result = cli("sites", "show", site, "--json")
    assert result.exit_code == 0, result.output
    return SiteInfo.model_validate_json(result.stdout)


def test_sites_list_with_no_sites(cli: CLI) -> None:
    result = cli("sites", "list")

    assert result.exit_code == 0
    assert "no sites" in result.stdout


def test_sites_list_lists_the_sites_by_key(cli: CLI, harness: Harness) -> None:
    async def sites(db: DBManager) -> None:
        for key, name in (("b.com", None), ("a.com", "Site A"), ("example.com", None)):
            await db.upsert_site(Site(key=key, name=name))

    harness.seed(sites)

    shown = cli("sites", "list").stdout
    listed = SiteList.model_validate_json(cli("sites", "list", "--json").stdout)
    found = SiteList.model_validate_json(cli("sites", "list", "--search", "exam", "--json").stdout)
    paged = SiteList.model_validate_json(cli("sites", "list", "-n", "1", "--json").stdout)

    assert shown.index("a.com") < shown.index("b.com") < shown.index("example.com")
    assert "Site A" in shown
    assert [site.key for site in listed.sites] == ["a.com", "b.com", "example.com"]
    assert [site.key for site in found.sites] == ["example.com"]
    assert [site.key for site in paged.sites] == ["a.com"]


def test_sites_set_stores_a_manual_strategy(cli: CLI) -> None:
    result = cli(
        "sites", "set", "https://www.example.com/watch", "--tool", "http",
        "--pattern", "^/watch", "-O", "chunk_size=10", "-O", "confidence=0.5",
        "--priority", "-1", "--requires-auth", "--json",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    strategy = Strategy.model_validate_json(result.stdout)
    assert (strategy.site_key, strategy.tool, strategy.url_pattern) == (
        "example.com",
        "http",
        "^/watch",
    )
    assert strategy.options == {"chunk_size": 10, "confidence": 0.5}
    assert (strategy.priority, strategy.requires_auth, strategy.source) == (
        -1,
        True,
        StrategySource.MANUAL,
    )
    assert site_info(cli, "example.com").strategies == [strategy]


def test_sites_set_says_what_it_set(cli: CLI) -> None:
    # (A value that is not JSON is text.)
    result = cli("sites", "set", "example.com", "--tool", "yt-dlp", "-O", "errors={}", "-O", "x=y")

    assert result.exit_code == 2  # (the fake yt-dlp has no option x)
    result = cli("sites", "set", "example.com", "--tool", "yt-dlp", "-O", "confidence=0.5")
    assert result.exit_code == 0, result.output
    assert "example.com" in result.stdout
    assert "yt-dlp" in result.stdout
    assert site_info(cli, "example.com").strategies[0].options == {"confidence": 0.5}


def test_sites_set_with_a_bad_option_is_a_usage_error(cli: CLI) -> None:
    for option in ("nope", "=1"):
        result = cli("sites", "set", "example.com", "--tool", "http", "-O", option)

        assert result.exit_code == 2
        assert "KEY=VALUE" in result.output


def test_sites_set_with_options_the_tool_does_not_have_is_a_usage_error(cli: CLI) -> None:
    result = cli("sites", "set", "example.com", "--tool", "http", "-O", "nope=1")

    assert result.exit_code == 2
    assert "nope" in result.stderr


def test_sites_set_with_a_bad_pattern_is_a_usage_error(cli: CLI) -> None:
    result = cli("sites", "set", "example.com", "--tool", "http", "--pattern", "(unclosed")

    assert result.exit_code == 2
    assert "pattern" in result.stderr


def test_sites_set_with_an_unknown_tool_fails(cli: CLI) -> None:
    assert cli("sites", "set", "example.com", "--tool", "nope").exit_code == 1


def test_sites_show_shows_the_site_and_its_strategies(cli: CLI) -> None:
    cli("sites", "set", "example.com", "--tool", "yt-dlp", "--pattern", "^/shorts/")
    cli("sites", "set", "example.com", "--tool", "http")

    result = cli("sites", "show", "www.example.com")

    assert result.exit_code == 0, result.output
    assert "example.com" in result.stdout
    assert "^/shorts/" in result.stdout
    assert "(whole site)" in result.stdout
    assert {s.tool for s in site_info(cli, "example.com").strategies} == {"yt-dlp", "http"}


def test_sites_show_of_an_unknown_site_fails(cli: CLI) -> None:
    result = cli("sites", "show", "example.com")

    assert result.exit_code == 1
    assert "knows nothing about example.com" in result.stderr
    assert "udl sites set" in result.stderr


def test_sites_show_of_something_that_is_not_a_site_is_a_usage_error(cli: CLI) -> None:
    assert cli("sites", "show", "not a site").exit_code == 2


def test_sites_rm_forgets_a_site(cli: CLI) -> None:
    cli("sites", "set", "example.com", "--tool", "http")

    result = cli("sites", "rm", "www.example.com")
    again = cli("sites", "rm", "example.com", "--json")

    assert result.exit_code == 0, result.output
    assert "Forgot example.com" in result.stdout
    assert again.exit_code == 1
    assert Deleted.model_validate_json(again.stdout) == Deleted(deleted=False)
    assert "knew nothing" in cli("sites", "rm", "example.com").stdout
