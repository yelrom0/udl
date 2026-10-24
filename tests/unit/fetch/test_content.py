"""
-------------------
- test_content.py -
-------------------

What a fetched page reads as (udl.fetch.content), on the anti-bot site's content pages: its main
content as Markdown (or text, or its HTML) with absolute links, or only what a selector matches;
charsets; JSON and plain text as they are; and files described, not converted
"""

# Package Imports
import pytest

# Local Imports
from support.antibot import CONTENT_DIR, AntiBotSite
from udl.errors import ConfigError
from udl.fetch.content import (
    FetchedPage,
    PageFormat,
    PageText,
    decode,
    html_title,
    is_javascript_shell,
    media_type,
)

BASE = "https://a.example/docs/widgets"


def fetched(
    path: str, content_type: str, *, url: str = BASE, body: bytes | None = None
) -> FetchedPage:
    kind, charset = media_type(content_type)
    return FetchedPage(
        url=url,
        final_url=url,
        status=200,
        content_type=kind,
        charset=charset,
        body=AntiBotSite.content(path) if body is None else body,
    )


def docs() -> FetchedPage:
    return fetched("/docs", "text/html; charset=utf-8")


def html(body: str, *, head: str = "") -> FetchedPage:
    page = f"<!doctype html><html><head>{head}</head><body>{body}</body></html>"
    return FetchedPage(
        url=BASE, final_url=BASE, status=200, content_type="text/html", body=page.encode()
    )


# ---------------------------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------------------------


def test_a_pages_main_content_reads_as_markdown_a_person_might_have_written() -> None:
    shown = PageText.render(docs(), PageFormat.MARKDOWN)

    assert shown.content is not None
    assert shown.title == "Widgets: a guide"
    content = shown.content
    assert content.startswith("# Widgets: a guide")
    assert "\n## Installing\n" in content
    assert "\n### Choosing one\n" in content
    assert "1. Download it from [the widget site](https://example.org/widget.tar.gz)." in content
    assert "- Small ones for toys" in content
    assert "```\nwidget install --all\nwidget check\n```" in content
    assert "| Size | Teeth | Price |" in content
    assert "| Medium | 24 | $5 |" in content
    assert "**motion**" in content


def test_its_links_are_made_absolute() -> None:
    content = PageText.render(docs(), PageFormat.MARKDOWN).content or ""

    assert "[gears](https://a.example/gears)" in content
    assert "[questions people ask](https://a.example/faq.html#sizes)" in content


def test_a_page_without_a_main_or_article_keeps_its_headings_too() -> None:
    soup = AntiBotSite.content("/docs").replace(b"<main>", b"").replace(b"</main>", b"")
    soup = soup.replace(b"<article>", b"<div>").replace(b"</article>", b"</div>")

    content = PageText.render(fetched("/docs", "text/html", body=soup), PageFormat.MARKDOWN).content

    assert content is not None
    assert content.startswith("# Widgets: a guide")
    assert "\n## Sizes\n" in content
    assert "Blog" not in content
    assert "Privacy" not in content


def test_the_menus_and_footers_are_left_out() -> None:
    content = PageText.render(docs(), PageFormat.MARKDOWN).content or ""

    assert "Blog" not in content
    assert "Privacy" not in content


def test_the_whole_page_can_be_had() -> None:
    content = PageText.render(docs(), PageFormat.MARKDOWN, main_only=False).content or ""

    assert "[Blog](https://a.example/blog)" in content
    assert "[Privacy](https://a.example/privacy)" in content
    assert "# Widgets: a guide" in content
    assert "| Small | 12 | $3 |" in content


def test_an_articles_images_are_absolute_too() -> None:
    article = fetched("/article", "text/html; charset=utf-8", url="https://news.example/2026/river")

    content = PageText.render(article, PageFormat.MARKDOWN).content or ""

    assert "# The river in winter" in content
    assert "https://news.example/2026/images/ice.jpg" in content
    assert "[the mill's history](https://news.example/history/mill)" in content
    assert "Most read" not in content
    assert "All rights reserved" not in content


def test_a_page_too_short_to_be_an_article_is_given_whole() -> None:
    shown = PageText.render(html("<p>Only <a href='/x'>this</a>.</p>"), PageFormat.MARKDOWN)

    assert shown.content == "Only [this](https://a.example/x)."


def test_a_base_href_is_what_links_are_relative_to() -> None:
    page = html("<p><a href='y'>Y</a></p>", head="<base href='https://cdn.example/files/'>")

    shown = PageText.render(page, PageFormat.MARKDOWN, main_only=False)

    assert shown.content == "[Y](https://cdn.example/files/y)"


def test_scripts_and_styles_are_never_content() -> None:
    page = html("<p>Text</p><script>var secret = 1;</script><style>p{}</style>")

    shown = PageText.render(page, PageFormat.MARKDOWN, main_only=False)

    assert shown.content == "Text"


# ---------------------------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------------------------


def test_select_gives_only_what_it_matches() -> None:
    shown = PageText.render(docs(), PageFormat.MARKDOWN, select="table")

    assert shown.content is not None
    assert shown.content.startswith("| Size | Teeth | Price |")
    assert "Installing" not in shown.content


def test_select_converts_each_match_whole() -> None:
    shown = PageText.render(docs(), PageFormat.MARKDOWN, select="h2")

    assert shown.content == "## Installing\n\n## Sizes"


def test_select_with_text_gives_their_text() -> None:
    shown = PageText.render(docs(), PageFormat.TEXT, select="ol li a")

    assert shown.content == "the widget site"


def test_select_with_html_gives_their_html_with_absolute_links() -> None:
    shown = PageText.render(docs(), PageFormat.HTML, select="article p:last-of-type a")

    assert shown.content == '<a href="https://a.example/faq.html#sizes">questions people ask</a>'


def test_select_that_matches_nothing_says_so() -> None:
    shown = PageText.render(docs(), PageFormat.MARKDOWN, select=".nothing")

    assert shown.content == ""
    assert shown.note == "Nothing on the page matches '.nothing'"


def test_a_selector_that_is_not_one_is_refused() -> None:
    with pytest.raises(ConfigError, match="not a CSS selector"):
        PageText.render(docs(), PageFormat.MARKDOWN, select="p[")


# ---------------------------------------------------------------------------------------------
# Text and HTML
# ---------------------------------------------------------------------------------------------


def test_text_is_the_main_content_without_markdown() -> None:
    content = PageText.render(docs(), PageFormat.TEXT).content or ""

    assert content.startswith("Widgets: a guide\n")
    assert "Widgets turn gears into motion." in content
    assert "](" not in content
    assert "Blog" not in content


def test_the_whole_page_as_text() -> None:
    content = PageText.render(docs(), PageFormat.TEXT, main_only=False).content or ""

    assert "Blog" in content
    assert "Widgets turn gears into motion." in content
    assert "**" not in content
    assert "#" not in content.replace("faq.html#sizes", "")


def test_html_is_the_page_as_it_was_read() -> None:
    shown = PageText.render(docs(), PageFormat.HTML)

    assert shown.content == AntiBotSite.content("/docs").decode()


def test_raw_is_the_page_as_it_was_served() -> None:
    shown = PageText.render(docs(), PageFormat.RAW, select="table")

    assert shown.content == AntiBotSite.content("/docs").decode()


# ---------------------------------------------------------------------------------------------
# Charsets
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "content_type"),
    [("/cp1252", "text/html; charset=windows-1252"), ("/cp1252-meta", "text/html")],
    ids=["in its header", "only in its meta"],
)
def test_a_page_in_windows_1252_comes_out_right(path: str, content_type: str) -> None:
    shown = PageText.render(fetched(path, content_type), PageFormat.MARKDOWN)

    assert shown.title == "Caf\u00e9 \u2013 la carte"
    assert "\u201cLe meilleur caf\u00e9 du quartier,\u201d" in (shown.content or "")
    assert "\u20ac3" in (shown.content or "")


def test_a_header_charset_beats_the_meta() -> None:
    body = '<meta charset="utf-8"><p>caf\u00e9</p>'.encode("cp1252")

    assert decode(body, "windows-1252", html=True) == (
        '<meta charset="utf-8"><p>caf\u00e9</p>',
        "cp1252",
    )


def test_without_a_charset_utf_8_is_tried_first() -> None:
    assert decode("caf\u00e9".encode(), None, html=False) == ("caf\u00e9", "utf-8")
    assert decode("caf\u00e9".encode(), None, html=True) == ("caf\u00e9", "utf-8")


def test_html_that_says_nothing_and_is_not_utf_8_is_windows_1252_as_browsers_read_it() -> None:
    body = "<p>Le caf\u00e9 cr\u00e8me co\u00fbte \u20ac3.</p>".encode("cp1252")

    assert decode(body, None, html=True) == (
        "<p>Le caf\u00e9 cr\u00e8me co\u00fbte \u20ac3.</p>",
        "cp1252",
    )


def test_other_text_that_says_nothing_is_guessed() -> None:
    russian = (CONTENT_DIR / "russian.txt").read_text()
    text, charset = decode(russian.encode("cp1251"), None, html=False)

    assert text == russian
    assert charset == "cp1251"


def test_an_unknown_charset_is_not_trusted() -> None:
    assert decode("caf\u00e9".encode(), "no-such-charset", html=False) == ("caf\u00e9", "utf-8")


def test_media_types_and_charsets_are_read_from_a_content_type() -> None:
    assert media_type('Text/HTML; Charset="ISO-8859-1"') == ("text/html", "ISO-8859-1")
    assert media_type("application/json") == ("application/json", None)
    assert media_type(None) == (None, None)
    assert media_type("text/plain; charset=") == ("text/plain", None)


def test_a_pages_title() -> None:
    assert html_title("<title>  A \n title </title>") == "A title"
    assert html_title("<p>none</p>") is None


# ---------------------------------------------------------------------------------------------
# What is not HTML
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "content_type"),
    [("/data.json", "application/json"), ("/notes.txt", "text/plain; charset=utf-8")],
)
@pytest.mark.parametrize("format_", list(PageFormat))
def test_json_and_plain_text_are_given_as_they_are(
    path: str, content_type: str, format_: PageFormat
) -> None:
    shown = PageText.render(fetched(path, content_type), format_)

    assert shown.content == AntiBotSite.content(path).decode()


@pytest.mark.parametrize("format_", list(PageFormat))
def test_a_file_is_described_not_converted(format_: PageFormat) -> None:
    pdf = fetched("/report.pdf", "application/pdf")

    shown = PageText.render(pdf, format_)

    assert shown.content is None
    assert (shown.media_type, shown.size) == ("application/pdf", len(pdf.body))
    assert shown.note == f"application/pdf, {len(pdf.body)} bytes: not converted to text"


def test_a_file_of_no_type_is_described_too() -> None:
    page = FetchedPage(url=BASE, final_url=BASE, status=200, body=b"\x00\x01")

    shown = PageText.render(page, PageFormat.MARKDOWN)

    assert shown.note == "a file of an unknown type, 2 bytes: not converted to text"


def test_xml_and_json_of_other_types_count_as_text() -> None:
    for kind in ("application/xml", "application/ld+json", "image/svg+xml", "text/csv"):
        page = FetchedPage(url=BASE, final_url=BASE, status=200, content_type=kind, body=b"<a/>")
        assert PageText.render(page, PageFormat.MARKDOWN).content == "<a/>"


# ---------------------------------------------------------------------------------------------
# A JavaScript shell (which the ladder sends to the browser)
# ---------------------------------------------------------------------------------------------


def test_an_empty_page_that_scripts_fill_in_is_a_javascript_shell() -> None:
    shell = html('<div id="app"></div><script src="/app.js"></script>')

    assert is_javascript_shell(shell)


def test_a_page_with_an_enable_javascript_notice_and_few_words_is_one() -> None:
    words = " ".join(["Loading the shop's front page"] * 6)
    notice = html(
        f"<p>{words}</p><noscript>Please enable JavaScript to use this site.</noscript>"
        "<script>start()</script>"
    )

    assert is_javascript_shell(notice)


def test_a_page_with_content_is_not_one() -> None:
    assert not is_javascript_shell(docs())


def test_a_page_of_few_words_without_scripts_is_not_one() -> None:
    assert not is_javascript_shell(html("<p>Short.</p>"))


def test_a_short_page_with_scripts_is_not_one() -> None:
    # (Most pages have some script, an analytics one at least.)
    assert not is_javascript_shell(html("<h1>Item</h1><p>In stock.</p><script>track()</script>"))


def test_a_page_of_scripts_and_no_words_is_one() -> None:
    assert is_javascript_shell(html('<script src="/bundle.js"></script>'))


def test_an_apps_empty_root_makes_a_page_one_whatever_else_it_says() -> None:
    words = " ".join(["A footer with links and words in it, as many have."] * 10)
    page = html(f'<div id="root"></div><footer>{words}</footer><script src="/app.js"></script>')

    assert is_javascript_shell(page)


def test_a_long_page_that_mentions_javascript_is_not_one() -> None:
    words = " ".join(["A long article about why sites require JavaScript now."] * 20)

    assert not is_javascript_shell(html(f"<p>{words}</p><script>x()</script>"))


def test_what_is_not_html_is_not_one() -> None:
    assert not is_javascript_shell(fetched("/data.json", "application/json"))
