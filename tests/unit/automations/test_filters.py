"""
-------------------
- test_filters.py -
-------------------

Filters: conditions on an event's data, as a tree (all / any / not over conditions) or as the
one-line shorthand, which round-trips. A filter is checked against its event types' fields when
it is saved (a field they lack, an operator on the wrong kind of value), and a field an event
lacks never matches (except under not). Regexes read at most 64 KiB of a field.
"""

# System Imports
from datetime import UTC, datetime
from typing import Any, Self

# Package Imports
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

# Local Imports
from udl.automations.events import EventType, EventTypes
from udl.automations.filters import (
    MAX_SEARCHED,
    AllOf,
    AnyOf,
    Condition,
    Filter,
    FilterField,
    Not,
    Op,
    check,
    failing,
    matches,
    parse,
    shorthand,
)
from udl.errors import ConfigError, ErrorCode, InvalidFilter


def c(field: str, op: Op = Op.TRUTHY, value: JsonValue = None, **more: Any) -> Condition:
    return Condition.model_validate({"field": field, "op": op, "value": value, **more})


# ---------------------------------------------------------------------------------------------
# The shorthand
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "tree"),
    [
        ("has_attachments", c("has_attachments")),
        ('labels contains "IMPORTANT"', c("labels", Op.CONTAINS, "IMPORTANT")),
        ('from.address endswith "@bank.com"', c("from.address", Op.ENDSWITH, "@bank.com")),
        ('a startswith "x"', c("a", Op.STARTSWITH, "x")),
        ('subject ~ "(?i)statement|invoice"', c("subject", Op.MATCHES, "(?i)statement|invoice")),
        ("size >= 1024", c("size", Op.GE, 1024)),
        ("size > 1.5", c("size", Op.GT, 1.5)),
        ("size < -3", c("size", Op.LT, -3)),
        ('at <= "2026-01-01T00:00:00Z"', c("at", Op.LE, "2026-01-01T00:00:00Z")),
        ("flag == true", c("flag", Op.EQ, True)),
        ("flag != false", c("flag", Op.NE, False)),
        ("gone == null", c("gone", Op.EQ, None)),
        ("error exists", c("error", Op.EXISTS)),
        ('name == i"bob"', c("name", Op.EQ, "bob", case_sensitive=False)),
        ('status in ["a", "b"]', c("status", Op.IN, ["a", "b"])),
        ("n in [1, 2.5, null, true]", c("n", Op.IN, [1, 2.5, None, True])),
        ('tag in [i"a", i"b"]', c("tag", Op.IN, ["a", "b"], case_sensitive=False)),
        ("not sent_by_udl", Not.of(c("sent_by_udl"))),
        ("not error exists", Not.of(c("error", Op.EXISTS))),
        (
            'channel.kind == "dm" and not sent_by_udl',
            AllOf.of(c("channel.kind", Op.EQ, "dm"), Not.of(c("sent_by_udl"))),
        ),
        (
            'from.address endswith "@bank.com" and (subject ~ "(?i)statement|invoice" or '
            "has_attachments)",
            AllOf.of(
                c("from.address", Op.ENDSWITH, "@bank.com"),
                AnyOf.of(c("subject", Op.MATCHES, "(?i)statement|invoice"), c("has_attachments")),
            ),
        ),
        # not binds tightest, then and, then or
        ("not a or b", AnyOf.of(Not.of(c("a")), c("b"))),
        ("a or b and c", AnyOf.of(c("a"), AllOf.of(c("b"), c("c")))),
        ("not (a and b)", Not.of(AllOf.of(c("a"), c("b")))),
        ("a and b and c", AllOf.of(c("a"), c("b"), c("c"))),
        ("a or b or c", AnyOf.of(c("a"), c("b"), c("c"))),
        ("(a or b) and c", AllOf.of(AnyOf.of(c("a"), c("b")), c("c"))),
        ("not not a", Not.of(Not.of(c("a")))),
        ('x == "a \\"quoted\\" \\\\ thing"', c("x", Op.EQ, 'a "quoted" \\ thing')),
        ('x == "naïve ✓"', c("x", Op.EQ, "naïve ✓")),
        # words that are operators elsewhere can name fields
        ("and == 1 or or", AnyOf.of(c("and", Op.EQ, 1), c("or"))),
        ("exists and in", AllOf.of(c("exists"), c("in"))),
        ("items.0.name", c("items.0.name")),
    ],
)
def test_the_shorthand_parses_to_its_tree_and_back(text: str, tree: Filter) -> None:
    assert parse(text) == tree
    assert shorthand(tree) == text


@pytest.mark.parametrize(
    ("text", "written"),
    [
        ("a and (b and c)", "a and b and c"),
        ("((a))", "a"),
        ("a   ==   1", "a == 1"),
        ("a==1 and(b)", "a == 1 and b"),
        ("(a and b) or c", "a and b or c"),
        ("x in []", "x in []"),
        ("x == 1e3", "x == 1000.0"),
    ],
)
def test_the_shorthand_is_written_with_the_fewest_parentheses(text: str, written: str) -> None:
    assert shorthand(parse(text)) == written


@pytest.mark.parametrize(
    ("text", "position", "said"),
    [
        ("", 1, "a field"),
        ("a ==", 5, "a value"),
        ('a == "x" and', 13, "a field"),
        ("(a", 3, ")"),
        ('a == "unclosed', 6, "string"),
        ('a in "x"', 6, "list"),
        ('a ~ "("', 5, "regex"),
        ('a == {"x": 1}', 6, "value"),
        ('a == [i"x", "y"]', 6, "i"),
        ("a b", 3, "b"),
        ("== 1", 1, "a field"),
        ("a == NaN", 6, "a value"),
        ("a == 1)", 7, ")"),
        ("not.a", 1, "a field"),
        ("a.", 1, "field"),
        ('a < "soon"', 5, "time"),
        ("a startswith 3", 14, "text"),
        ("a contains [1]", 12, "list"),
        ("a , b", 3, ","),
        ("a in [1 2]", 9, ", or ]"),
    ],
)
def test_a_bad_shorthand_says_where(text: str, position: int, said: str) -> None:
    with pytest.raises(InvalidFilter) as raised:
        parse(text)

    assert f"at {position}" in raised.value.message
    assert said in raised.value.message
    assert raised.value.code is ErrorCode.INVALID_FILTER
    assert isinstance(raised.value, ConfigError)


# ---------------------------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------------------------


def test_the_tree_is_plain_json() -> None:
    tree = parse('not (a == 1 or b ~ i"x")')

    assert tree.model_dump(mode="json") == {
        "not": {
            "any": [
                {"field": "a", "op": "eq", "value": 1, "case_sensitive": True},
                {"field": "b", "op": "matches", "value": "x", "case_sensitive": False},
            ]
        }
    }


def test_a_tree_or_the_shorthand_is_taken_where_a_filter_goes() -> None:
    class Holder(BaseModel):
        model_config = ConfigDict(frozen=True)

        filter: FilterField | None = None

    tree = {"all": [{"field": "a"}, {"not": {"field": "b", "op": "exists"}}]}

    from_text = Holder.model_validate({"filter": "a and not b exists"})
    from_tree = Holder.model_validate({"filter": tree})

    assert from_text == from_tree
    assert from_tree.filter == AllOf.of(c("a"), Not.of(c("b", Op.EXISTS)))
    assert Holder.model_validate_json(from_tree.model_dump_json()) == from_tree
    with pytest.raises(ValidationError, match="at 5"):
        Holder.model_validate({"filter": "a =="})


def test_an_all_or_any_of_one_filter_is_that_filter() -> None:
    class Holder(BaseModel):
        filter: FilterField

    assert Holder.model_validate({"filter": {"all": [{"field": "a"}]}}).filter == c("a")
    with pytest.raises(ValidationError):
        Holder.model_validate({"filter": {"any": []}})


def test_nested_alls_are_one_all() -> None:
    nested = {"all": [{"all": [{"field": "a"}, {"field": "b"}]}, {"field": "c"}]}

    assert AllOf.of(c("a"), AllOf.of(c("b"), c("c"))) == AllOf.of(c("a"), c("b"), c("c"))
    assert AllOf.model_validate(nested) == AllOf.of(c("a"), c("b"), c("c"))
    assert AnyOf.of(AnyOf.of(c("a"), c("b")), c("c")) == AnyOf.of(c("a"), c("b"), c("c"))


@pytest.mark.parametrize(
    ("fields", "said"),
    [
        ({"field": "a", "op": "exists", "value": 1}, "no value"),
        ({"field": "a", "op": "truthy", "value": "x"}, "no value"),
        ({"field": "a", "op": "in", "value": 1}, "list"),
        ({"field": "a", "op": "contains", "value": [1]}, "list"),
        ({"field": "a", "op": "startswith", "value": 1}, "text"),
        ({"field": "a", "op": "lt", "value": True}, "number"),
        ({"field": "a", "op": "eq", "value": float("inf")}, "finite"),
        ({"field": "a b", "op": "eq", "value": 1}, "field"),
        ({"field": "not.x"}, "field"),
    ],
)
def test_a_condition_s_value_fits_its_operator(fields: dict[str, Any], said: str) -> None:
    with pytest.raises(ValidationError, match=said):
        Condition.model_validate(fields)


@pytest.mark.parametrize(
    ("model", "given"),
    [(Condition, 5), (AllOf, 5), (AllOf, {"all": 5}), (AnyOf, "a"), (Not, None)],
)
def test_what_is_not_a_filter_is_refused(model: type[BaseModel], given: object) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(given)


def test_case_matters_only_for_text() -> None:
    assert c("a", Op.EQ, 1, case_sensitive=False).case_sensitive is True
    assert c("a", Op.EQ, "x", case_sensitive=False).case_sensitive is False
    assert c("a", Op.IN, [1, "x"], case_sensitive=False).case_sensitive is False


# ---------------------------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------------------------

DATA: dict[str, JsonValue] = {
    "from": {"address": "Bank@Bank.com", "name": "Bank"},
    "to": [{"address": "me@x.com"}, {"address": "you@y.com"}],
    "subject": "Your Statement",
    "labels": ["INBOX", "IMPORTANT"],
    "size": 2048,
    "ratio": 0.5,
    "has_attachments": True,
    "received_at": "2026-01-02T03:04:05Z",
    "count": 0,
    "empty": "",
    "nothing": None,
    "flag": False,
    "nested": [[{"x": 1}], [{"x": 2}]],
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # text, case-sensitive unless i"…"
        ('from.address endswith "@Bank.com"', True),
        ('from.address endswith "@bank.com"', False),
        ('from.address endswith i"@bank.com"', True),
        ('from.name startswith "Ba"', True),
        ('from.name == "bank"', False),
        ('from.name == i"BANK"', True),
        ('from.name != "Bank"', False),
        ('subject contains "State"', True),
        ('subject contains i"STATE"', True),
        ('subject contains "state"', False),
        ('size contains "2"', False),
        ('from == "x"', False),
        ('labels == "INBOX"', False),
        ("subject > 3", False),
        ('subject ~ "(?i)statement|invoice"', True),
        ('subject ~ "^Statement"', False),
        ('subject ~ i"^your"', True),
        # through a list: any item
        ('to.address == "you@y.com"', True),
        ('to.address startswith "nobody"', False),
        ('to.address != "me@x.com"', True),
        ("nested.x == 2", True),
        # a list's items
        ('labels contains "IMPORTANT"', True),
        ('labels contains i"important"', True),
        ('labels contains "SPAM"', False),
        ('labels in ["INBOX"]', True),
        ('labels startswith "IMP"', True),
        ('labels == ["INBOX", "IMPORTANT"]', True),
        ('labels == ["INBOX"]', False),
        # numbers and times
        ("size > 1024", True),
        ("size <= 2048", True),
        ("size < 2048", False),
        ("size == 2048.0", True),
        ("ratio >= 0.5", True),
        ('size > "2026-01-01T00:00:00Z"', False),  # (a number is no time)
        ('received_at > "2026-01-01T00:00:00Z"', True),
        ('received_at < "2026-01-02T04:04:05+01:00"', False),
        ('received_at <= "2026-01-02T04:04:05+01:00"', True),
        ('received_at >= "2026-01-02T03:04:05"', True),  # (no zone: UTC)
        ('subject > "2026-01-01T00:00:00Z"', False),  # (not a time)
        ('size startswith "2"', False),
        # in
        ("size in [1, 2048]", True),
        ('size in ["2048"]', False),
        ('from.name in [i"bank", i"other"]', True),
        # yes or no
        ("has_attachments", True),
        ("flag", False),
        ("count", False),
        ("empty", False),
        ("nothing", False),
        ("labels", True),
        ("flag == false", True),
        ("flag == 0", False),
        ("count == false", False),
        ("nothing == null", True),
        # what an event lacks never matches, except under not
        ("missing", False),
        ("not missing", True),
        ("missing == 1", False),
        ("not missing == 1", True),
        ("missing != 1", False),
        ("missing.deeper exists", False),
        ("from.missing", False),
        ("subject.deeper", False),
        # exists: there, and not null
        ("subject exists", True),
        ("nothing exists", False),
        ("not nothing exists", True),
        ("missing exists", False),
        # combined
        ('from.address endswith i"@bank.com" and (subject ~ "invoice" or has_attachments)', True),
        ('subject ~ "invoice" or flag', False),
        ("not (has_attachments and flag)", True),
    ],
)
def test_a_filter_matches_an_event_s_data(text: str, expected: bool) -> None:
    assert matches(parse(text), DATA) is expected


def test_a_regex_reads_at_most_64_kib_of_a_field() -> None:
    late = {"body": "x" * MAX_SEARCHED + "NEEDLE"}
    early = {"body": "NEEDLE" + "x" * MAX_SEARCHED}

    assert MAX_SEARCHED == 64 * 1024
    assert matches(parse('body ~ "NEEDLE"'), late) is False
    assert matches(parse('body ~ "NEEDLE"'), early) is True


def test_a_regex_runs_in_linear_time() -> None:
    # (A pattern that backtracks for ever in Python's re finishes at once in RE2.)
    evil = parse('body ~ "(a+)+$"')

    assert matches(evil, {"body": "a" * 5000 + "!"}) is False


def test_a_regex_re2_cannot_run_is_refused() -> None:
    with pytest.raises(InvalidFilter, match="regex"):
        parse('body ~ "(?=lookahead)"')


@pytest.mark.parametrize(
    ("text", "failed"),
    [
        ('subject contains "State" and size > 1', None),
        ('subject contains "State" and size > 5000', "size > 5000"),
        ("flag or count", "flag or count"),
        ("not has_attachments", "not has_attachments"),
        ("(flag or has_attachments) and (count or empty)", "count or empty"),
    ],
)
def test_the_condition_that_failed_is_named(text: str, failed: str | None) -> None:
    assert failing(parse(text), DATA) == failed


# ---------------------------------------------------------------------------------------------
# Checking a filter against its event types
# ---------------------------------------------------------------------------------------------


class _Data(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class Address(_Data):
    address: str
    """Where."""


class Mail(_Data):
    subject: str
    """The subject."""
    sender: Address
    """Who sent it."""
    to: list[Address] = Field(default_factory=list[Address])
    """Who it went to."""
    labels: list[str] = Field(default_factory=list[str])
    """Its labels."""
    size: int = 0
    """Bytes."""
    received_at: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    """When."""
    flag: bool = False
    """Yes or no."""
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    """Headers."""
    extra: JsonValue = None
    """Anything."""

    @classmethod
    def example(cls) -> Self:
        return cls(subject="Hi", sender=Address(address="a@b"))


MAIL = EventType(
    name="test.mail",
    data=Mail,
    summary="Mail.",
    example=Mail.example(),
    subject=lambda mail: mail.subject,
)


class Other(_Data):
    subject: str
    """The subject."""


OTHER = EventType(
    name="test.other", data=Other, summary="Other.", example=Other(subject="x"), subject=str
)


@pytest.mark.parametrize(
    "text",
    [
        'subject == "x"',
        'sender.address endswith "@bank.com"',
        'to.address endswith "x"',
        'labels contains "X"',
        'labels startswith "X"',
        'labels == ["A", "B"]',
        'subject contains "x"',
        'not (subject == "x" or flag) and size > 1',
        'labels in ["A"]',
        "size > 3",
        "size in [1, 2]",
        'received_at > "2026-01-01T00:00:00Z"',
        "flag",
        "flag == true",
        "not sender exists",
        'headers.anything == "x"',
        "extra.deep.path == 1",
        "subject == null",
    ],
)
def test_a_filter_that_fits_its_type_passes(text: str) -> None:
    check(parse(text), [MAIL])


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ('sender.adress == "x"', "sender.adress"),
        ("nothing", "nothing"),
        ('size contains "x"', "size"),
        ("subject > 3", "subject"),
        ('size > "x"', "size"),
        ('subject < "2026-01-01T00:00:00Z"', "subject"),
        ("labels contains 3", "labels"),
        ('size startswith "x"', "size"),
        ('flag == "yes"', "flag"),
        ("subject in [1]", "subject"),
        ('sender == "x"', "sender"),
        ("received_at > 3", "received_at"),
        ('not (subject == "x" or nothing)', "nothing"),
        ("size > 1 and (flag or nothing)", "nothing"),
    ],
)
def test_a_filter_that_does_not_fit_its_type_is_refused(text: str, said: str) -> None:
    with pytest.raises(InvalidFilter, match=said):
        check(parse(text), [MAIL])


def test_a_refused_field_lists_the_type_s_fields() -> None:
    with pytest.raises(InvalidFilter) as raised:
        check(parse("sender.adress"), [MAIL])

    assert "test.mail" in raised.value.message
    assert raised.value.hint is not None
    assert "sender.address" in raised.value.hint


def test_a_value_its_field_cannot_have_is_refused() -> None:
    failed = EventTypes.default().get("udl.download.failed")

    check(parse('error.code == "network_error"'), [failed])
    check(parse('error.code == i"NETWORK_ERROR"'), [failed])
    with pytest.raises(InvalidFilter, match="network_eror"):
        check(parse('error.code in ["network_eror"]'), [failed])


def test_a_field_must_be_in_each_type() -> None:
    check(parse('subject == "x"'), [MAIL, OTHER])

    with pytest.raises(InvalidFilter, match=r"test\.other"):
        check(parse("size > 1"), [MAIL, OTHER])


def test_a_custom_type_takes_any_field() -> None:
    custom = EventTypes.default().get("custom.hello")

    check(parse('anything.at.all == "x" and n > 3'), [custom])


# ---------------------------------------------------------------------------------------------
# Round trips (hypothesis)
# ---------------------------------------------------------------------------------------------

_names = st.from_regex(r"[a-z_][a-z0-9_]{0,5}", fullmatch=True).filter(lambda n: n != "not")
_paths = st.lists(_names, min_size=1, max_size=3).map(".".join)
_text = st.text(max_size=12)
_numbers = st.integers(-(10**15), 10**15) | st.floats(allow_nan=False, allow_infinity=False)
_scalars = st.none() | st.booleans() | _numbers | _text
_times = st.datetimes(timezones=st.just(UTC)).map(lambda when: when.isoformat())
_patterns = st.sampled_from(["^a", "b$", "(?i)x|y", "[0-9]+", r"\d{2,3}", "a.c"])


@st.composite
def _conditions(draw: st.DrawFn) -> Condition:
    op = draw(st.sampled_from(list(Op)))
    value: object
    if op in (Op.TRUTHY, Op.EXISTS):
        value = None
    elif op is Op.IN:
        value = draw(st.lists(_scalars, max_size=3))
    elif op in (Op.STARTSWITH, Op.ENDSWITH):
        value = draw(_text)
    elif op is Op.MATCHES:
        value = draw(_patterns)
    elif op in (Op.LT, Op.LE, Op.GT, Op.GE):
        value = draw(_numbers | _times)
    elif op is Op.CONTAINS:
        value = draw(_scalars)
    else:
        value = draw(_scalars | st.lists(_scalars, max_size=3))
    return Condition.model_validate(
        {"field": draw(_paths), "op": op, "value": value, "case_sensitive": draw(st.booleans())}
    )


def _combined(children: st.SearchStrategy[Filter]) -> st.SearchStrategy[Filter]:
    many = st.lists(children, min_size=2, max_size=3)
    return (
        many.map(lambda found: AllOf.model_validate({"all": found}))
        | many.map(lambda found: AnyOf.model_validate({"any": found}))
        | children.map(Not.of)
    )


_filters: st.SearchStrategy[Filter] = st.recursive(_conditions(), _combined, max_leaves=8)


@settings(max_examples=300)
@given(tree=_filters)
def test_the_shorthand_of_any_filter_parses_back_to_it(tree: Filter) -> None:
    assert parse(shorthand(tree)) == tree


@settings(max_examples=200)
@given(tree=_filters)
def test_writing_a_parsed_shorthand_again_changes_nothing(tree: Filter) -> None:
    written = shorthand(tree)

    assert shorthand(parse(written)) == written


@settings(max_examples=100)
@given(tree=_filters)
def test_any_filter_round_trips_as_json(tree: Filter) -> None:
    class Holder(BaseModel):
        filter: FilterField

    holder = Holder(filter=tree)

    assert Holder.model_validate_json(holder.model_dump_json()) == holder
