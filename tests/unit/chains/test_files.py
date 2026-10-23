"""
-----------------
- test_files.py -
-----------------

Chain files: sites' tag chains as YAML (a document each, or a list in one), read and written,
with errors that say which chain is wrong and why
"""

# Package Imports
import pytest
from hypothesis import given
from hypothesis import strategies as st

# Local Imports
from support.chains import chains
from udl.chains.files import read_chains, write_chains
from udl.chains.models import FetchMode, Pick, SelectStep, SiteChain, TagChain
from udl.errors import ConfigError

FILE = """\
site: www.example.com
pattern: ^/watch/
fetch: static
headers: {Referer: "{url}"}
steps:
  - select: iframe#player
    attr: src
  - select: "video > source[type='video/mp4']"
    attr: src
    pick: best
handoff: http
---
site: other.org
steps: "click:#play >> network:\\\\.m3u8 | all"
priority: -1
requires_auth: true
"""


def test_reading_a_chain_file() -> None:
    first, second = read_chains(FILE)

    assert (first.site, first.pattern, first.fetch, first.handoff) == (
        "example.com",
        "^/watch/",
        FetchMode.STATIC,
        "http",
    )
    assert first.headers == {"Referer": "{url}"}
    assert first.steps[-1] == SelectStep(
        select="video > source[type='video/mp4']", attr="src", pick=Pick.BEST
    )
    assert (second.site, second.fetch, second.priority, second.requires_auth) == (
        "other.org",
        FetchMode.BROWSER,
        -1,
        True,
    )


def test_a_document_can_list_chains() -> None:
    found = read_chains("- {site: a.com, steps: 'video@src'}\n- {site: b.com, steps: 'a@href'}\n")

    assert [chain.site for chain in found] == ["a.com", "b.com"]


def test_empty_documents_are_nothing() -> None:
    assert read_chains("") == []
    assert read_chains("---\n---\n# nothing\n") == []


def test_writing_chains_writes_a_document_each() -> None:
    chain = SiteChain.model_validate(
        {
            "site": "example.com",
            "pattern": "^/watch/",
            "steps": "iframe#player@src >> script#data::text >> json:video.url | best",
            "headers": {"Referer": "{url}"},
            "handoff": "yt-dlp",
        }
    )
    plain = SiteChain.model_validate({"site": "other.org", "steps": "video@src"})

    written = write_chains([chain, plain])

    assert written == (
        "site: example.com\n"
        "pattern: ^/watch/\n"
        "fetch: static\n"
        "headers:\n"
        "  Referer: '{url}'\n"
        "steps:\n"
        "- select: iframe#player\n"
        "  attr: src\n"
        "- follow: true\n"
        "- select: script#data\n"
        "  text: true\n"
        "- json: video.url\n"
        "  pick: best\n"
        "handoff: yt-dlp\n"
        "---\n"
        "site: other.org\n"
        "fetch: static\n"
        "steps:\n"
        "- select: video\n"
        "  attr: src\n"
    )
    assert read_chains(written) == [chain, plain]


def test_unusual_characters_read_back_as_they_were() -> None:
    # (U+0085 is a line break to YAML 1.1, as PyYAML reads it, unless it is escaped.)
    chain = SiteChain.model_validate(
        {"site": "example.com", "steps": [{"regex": "\x85"}], "headers": {"X": "日本 ✓"}}
    )

    assert read_chains(write_chains([chain])) == [chain]


def test_writing_no_chains_writes_nothing() -> None:
    assert write_chains([]) == ""


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("site: [unclosed", "not valid YAML"),
        ("just text", "chain 1: a chain is a mapping"),
        ("site: example.com", "chain 1: steps: Field required"),
        ("site: a.com\nsteps: video@src\n---\nsite: b.com\nsteps: video source", "chain 2: steps"),
        ("- {site: a.com, steps: video@src}\n- 42", "chain 2: a chain is a mapping"),
        ("site: not a url\nsteps: video@src", "chain 1: site"),
    ],
)
def test_a_chain_file_with_a_problem_says_which_chain(text: str, problem: str) -> None:
    with pytest.raises(ConfigError, match="chain file") as error:
        read_chains(text)

    assert problem in str(error.value)


@given(chains(), st.sampled_from(["example.com", "a.b.example.co.uk"]), st.booleans())
def test_chains_written_and_read_back_are_the_same(
    chain: TagChain, site: str, with_pattern: bool
) -> None:
    site_chain = SiteChain.model_validate(
        {
            **chain.model_dump(),
            "site": site,
            "pattern": "^/watch/" if with_pattern else None,
            "priority": 3,
        }
    )

    assert read_chains(write_chains([site_chain])) == [site_chain]
