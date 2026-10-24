"""
--------------------
- test_templates.py -
--------------------

Templates: what an action's arguments and an AI's prompts are written in. They render event
data that strangers wrote, so they run in a sandbox: no files, no imports, nothing private, and
no way to make udl run out of memory or time. A value that is a single {{ expression }} keeps
its type.
"""

# System Imports
import time
from datetime import UTC, datetime
from typing import Any

# Package Imports
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import JsonValue

# Local Imports
from udl.automations.templates import Limits, Templates
from udl.errors import ConfigError, ErrorCode, TemplateFailed

# Small limits, so tests that reach them are quick.
SMALL = Limits(output=4096, steps=1000)

EVENT = {
    "type": "gmail.message.received",
    "subject": "Mail from bank@example.com: Your statement",
    "data": {
        "from": {"address": "bank@example.com"},
        "body_text": "See https://bank.example.com/s/1. Or (https://example.org/x).",
        "labels": ["INBOX", "IMPORTANT"],
        "size": 1234,
    },
}


@pytest.fixture
def templates() -> Templates:
    return Templates(SMALL)


def render(templates: Templates, source: str, **context: Any) -> str:
    return templates.render_text(source, {"event": EVENT, **context})


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------


def test_a_template_renders_the_event(templates: Templates) -> None:
    rendered = render(
        templates,
        "{{ event.subject }} ({{ event.data['from'].address }}, {{ event.data.labels | length }})",
    )

    assert rendered == "Mail from bank@example.com: Your statement (bank@example.com, 2)"


def test_nothing_is_escaped_since_nothing_is_html(templates: Templates) -> None:
    assert render(templates, "{{ text }}", text="<b>&") == "<b>&"


def test_urls_finds_the_links_in_a_text(templates: Templates) -> None:
    rendered = render(templates, "{{ event.data.body_text | urls | join(' ') }}")

    assert rendered == "https://bank.example.com/s/1 https://example.org/x"


def test_tojson_writes_json_as_it_is(templates: Templates) -> None:
    rendered = render(templates, "{{ event.data['from'] | tojson }} {{ when | tojson }}", when=T)

    assert rendered == '{"address": "bank@example.com"} "2026-01-02T03:04:05Z"'


def test_truncate_shortens_a_text(templates: Templates) -> None:
    assert render(templates, "{{ ('a' * 20) | truncate(9, true, '…', 0) }}") == "aaaaaaaa…"


def test_a_template_with_a_syntax_error_says_where(templates: Templates) -> None:
    with pytest.raises(TemplateFailed, match="line 2") as raised:
        templates.check("fine\n{{ event.subject ")

    assert raised.value.code is ErrorCode.TEMPLATE_FAILED
    assert isinstance(raised.value, ConfigError)


def test_a_good_template_checks(templates: Templates) -> None:
    templates.check("{% for label in event.data.labels %}{{ label }}{% endfor %}")


def test_an_undefined_name_fails_with_the_template_s_line(templates: Templates) -> None:
    with pytest.raises(TemplateFailed, match=r"line 3.*nope"):
        render(templates, "one\ntwo {{ event.subject }}\nthree {{ event.data.nope }}")


def test_an_undefined_name_alone_fails_too(templates: Templates) -> None:
    with pytest.raises(TemplateFailed, match="nope"):
        templates.render_value("{{ nope }}", {})


def test_a_value_that_is_not_one_expression_is_a_template_error(templates: Templates) -> None:
    with pytest.raises(TemplateFailed, match="chunk after expression"):
        templates.render_value("{{ a b }}", {"a": 1, "b": 2})


@pytest.mark.parametrize(
    ("source", "rendered"),
    [
        (
            "{{ 'ab'.zfill(4) }}|{{ 'a\tb'.expandtabs() }}|{{ 'a-b'.replace('-', '+') }}",
            "00ab|a       b|a+b",
        ),
        ("{{ ', '.join(event.data.labels) }}", "INBOX, IMPORTANT"),
        ("{{ [event.data['from']] | join(', ', attribute='address') }}", "bank@example.com"),
        ("{{ 'a\nb' | indent('> ', true) }}", "> a\n> b"),
        (
            "{{ 'ab' | center(6) }}|{{ 'x' * 3 }}|{{ 3 * 'x' }}|{{ 2 ** 64 }}",
            "  ab  |xxx|xxx|18446744073709551616",
        ),
        ("{{ '{:>5}|{}'.format('a', 'b') }}", "    a|b"),
        ("{{ none | urls }}|{{ (1, 2) | tojson }}|{{ {'a': 1} | length }}", "[]|[1, 2]|1"),
        ("{{ dict(a=1, b='x') | tojson }}|{{ range(3) | list }}", '{"a": 1, "b": "x"}|[0, 1, 2]'),
        ("{{ 7 // 2 }} {{ 7 - 2 }} {{ 7 % 2 }}", "3 5 1"),
    ],
)
def test_what_stays_within_the_limits_renders_as_jinja_s_would(
    templates: Templates, source: str, rendered: str
) -> None:
    assert render(templates, source) == rendered


def test_a_failing_expression_is_a_template_error(templates: Templates) -> None:
    with pytest.raises(TemplateFailed, match="division by zero"):
        render(templates, "{{ 1 / 0 }}")
    with pytest.raises(TemplateFailed, match="division by zero"):
        templates.render_value("{{ 1 / 0 }}", {})


def test_a_macro_that_never_ends_is_a_template_error(templates: Templates) -> None:
    with pytest.raises(TemplateFailed):
        render(templates, "{% macro again() %}{{ again() }}{% endmacro %}{{ again() }}")


# ---------------------------------------------------------------------------------------------
# Values: a lone {{ expression }} keeps its type
# ---------------------------------------------------------------------------------------------

T = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

_json: st.SearchStrategy[JsonValue] = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=8), inner, max_size=4)
    ),
    max_leaves=10,
)


@settings(max_examples=100)
@given(value=_json)
def test_a_lone_expression_keeps_its_value_s_type(value: JsonValue) -> None:
    assert Templates(SMALL).render_value("{{ x }}", {"x": value}) == value
    assert Templates(SMALL).render_value(" {{- x -}} ", {"x": value}) == value


def test_values_are_rendered_all_through(templates: Templates) -> None:
    rendered = templates.render_value(
        {
            "urls": "{{ event.data.body_text | urls }}",
            "title": "Statement from {{ event.data['from'].address }}",
            "size": "{{ event.data.size }}",
            "nested": [{"labels": "{{ event.data.labels }}"}, 3, None, True],
            "plain": "no template here",
        },
        {"event": EVENT},
    )

    assert rendered == {
        "urls": ["https://bank.example.com/s/1", "https://example.org/x"],
        "title": "Statement from bank@example.com",
        "size": 1234,
        "nested": [{"labels": ["INBOX", "IMPORTANT"]}, 3, None, True],
        "plain": "no template here",
    }


def test_a_lone_expression_s_value_is_json(templates: Templates) -> None:
    assert templates.render_value("{{ when }}", {"when": T}) == "2026-01-02T03:04:05Z"
    assert templates.render_value("{{ (1, 2) }}", {}) == [1, 2]


def test_a_lone_expression_that_is_not_data_is_a_template_error(templates: Templates) -> None:
    with pytest.raises(TemplateFailed, match="not JSON"):
        templates.render_value("{{ range }}", {})


def test_two_expressions_are_text(templates: Templates) -> None:
    assert templates.render_value("{{ a }}{{ b }}", {"a": 1, "b": 2}) == "12"
    assert templates.render_value("{{ a }} }}", {"a": 1}) == "1 }}"


# ---------------------------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------------------------

_PRIVATE = st.sampled_from(
    ["__class__", "__globals__", "__init__", "__mro__", "__subclasses__", "__dict__", "__code__"]
) | st.from_regex(r"_[a-z_]{1,10}", fullmatch=True)


@settings(max_examples=50)
@given(attribute=_PRIVATE, base=st.sampled_from(["event", "''", "range", "dict", "x"]))
def test_nothing_private_is_ever_reached(attribute: str, base: str) -> None:
    for source in (f"{{{{ {base}.{attribute} }}}}", f"{{{{ {base}['{attribute}'] }}}}"):
        try:
            rendered = Templates(SMALL).render_text(source, {"event": EVENT, "x": object()})
        except TemplateFailed:
            continue
        # (Getting an item by a private name is allowed only where there is no such thing.)
        pytest.fail(f"{source} gave {rendered!r}")


@settings(max_examples=30)
@given(
    tag=st.sampled_from(
        [
            "{{% include {name} %}}",
            "{{% import {name} as m %}}",
            "{{% from {name} import x %}}",
            "{{% extends {name} %}}",
        ]
    )
)
def test_no_file_is_ever_read(tag: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    secret = tmp_path_factory.mktemp("files") / "secret.txt"
    secret.write_text("the secret")
    for name in (repr(str(secret)), repr("secret.txt"), "'/etc/passwd'"):
        with pytest.raises(TemplateFailed):
            Templates(SMALL).render_text(tag.format(name=name), {})


@pytest.mark.parametrize(
    "source",
    [
        "{{ range.__init__.__globals__.os }}",
        "{{ cycler.__init__ }}",  # (only range and dict are given)
        "{{ namespace(a=1) }}",
        "{{ joiner() }}",
        "{{ ''.__class__.__mro__[1].__subclasses__() }}",
        "{{ lipsum(n=1000) }}",
        "{{ range.__self__ }}",
        "{% set l = [] %}{{ l.append(1) }}",  # (nothing can be changed in place)
        "{{ event.data.update({}) }}",
        "{{ '%999999d' % 1 }}",
        "{{ '{:>999999}'.format(1) }}",
        "{{ '{:{w}}'.format(1, w=9) }}",
        "{{ 10 ** 100000 }}",
        "{% macro square(n, k) %}{% if k %}{{ square(n * n, k - 1) }}{% else %}{{ n }}{% endif %}"
        "{% endmacro %}{{ square(3, 30) }}",
        "{{ 'x'.replace('x') }}",
    ],
)
def test_what_could_reach_out_of_the_sandbox_is_refused(templates: Templates, source: str) -> None:
    with pytest.raises(TemplateFailed):
        render(templates, source)


# ---------------------------------------------------------------------------------------------
# Limits: no rendering runs udl out of memory or time
# ---------------------------------------------------------------------------------------------

_GROWTH = st.sampled_from(
    [
        "s ~ s",
        "s + s",
        "s * 2",
        "s * 1000000",
        "s | replace('a', s)",
        "[s, s] | join(s)",
        "s.replace('a', s)",
        "s.ljust(1000000000)",
        "s.center(1000000000)",
        "s | center(1000000000)",
        "s | indent(1000000000)",
        "s.join([s] * 3)",
        "s ~ (s | tojson)",
        "'{:>99999999}'.format(s)",
        "s.expandtabs(100000000)",
    ]
)


@settings(max_examples=60, deadline=None)
@given(growth=_GROWTH, rounds=st.integers(1, 60))
def test_output_and_what_is_made_on_the_way_are_capped(growth: str, rounds: int) -> None:
    # (Text that grows each time a macro calls itself: there is no other way to keep it.)
    source = (
        "{% macro grow(s, n) %}{% if n > 0 %}"
        f"{{{{ grow({growth}, n - 1) }}}}"
        "{% else %}{{ s }}{% endif %}{% endmacro %}"
        f"{{{{ grow('a\\t', {rounds}) }}}}"
    )
    started = time.monotonic()

    try:
        rendered = Templates(SMALL).render_text(source, {})
    except TemplateFailed:
        rendered = ""

    assert len(rendered) <= SMALL.output
    assert time.monotonic() - started < 2


def test_output_is_capped(templates: Templates) -> None:
    with pytest.raises(TemplateFailed, match="4096"):
        render(templates, "{% for i in range(1000) %}0123456789{% endfor %}")


def test_loops_are_capped(templates: Templates) -> None:
    with pytest.raises(TemplateFailed, match="1000"):
        render(
            templates, "{% for i in range(100) %}{% for j in range(100) %}{% endfor %}{% endfor %}"
        )


def test_a_value_is_capped_like_text(templates: Templates) -> None:
    with pytest.raises(TemplateFailed, match="4096"):
        templates.render_value("{{ x }}", {"x": ["y" * 100] * 100})


def test_a_template_within_the_limits_renders(templates: Templates) -> None:
    rendered = render(templates, "{% for i in range(400) %}{{ i % 10 }}{% endfor %}")

    assert len(rendered) == 400


def test_a_template_is_capped_too(templates: Templates) -> None:
    with pytest.raises(TemplateFailed, match="65536"):
        Templates().render_text("x" * (64 * 1024 + 1), {})
    with pytest.raises(TemplateFailed, match="65536"):
        Templates().check("x" * (64 * 1024 + 1))


def test_a_value_checks_against_an_example(templates: Templates) -> None:
    templates.check_value({"to": "{{ event.data['from'].address }}", "n": 3}, {"event": EVENT})

    with pytest.raises(TemplateFailed, match="nope"):
        templates.check_value({"to": "{{ event.data.nope }}"}, {"event": EVENT})


def test_the_default_limits_are_generous() -> None:
    rendered = Templates().render_text("{% for i in range(10000) %}{{ 'x' * 10 }}{% endfor %}", {})

    assert len(rendered) == 100_000
