"""
--------------
- filters.py -
--------------

Filters: which events a subscription wants, as conditions on their data. A filter is a tree of
`all`, `any` and `not` over conditions, each on a dotted path into the event's data, or the same
written as a line of shorthand, which is taken wherever a filter is (FilterField):

    from.address endswith "@bank.com" and (subject ~ "(?i)statement|invoice" or has_attachments)
    channel.kind == "dm" and not sent_by_udl
    labels contains "IMPORTANT"

The shorthand's grammar (`not` binds tightest, then `and`, then `or`; parentheses group):

    filter    := or
    or        := and ("or" and)*
    and       := unary ("and" unary)*
    unary     := "not" unary | "(" filter ")" | condition
    condition := path [op value] | path "exists"
    op        := "==" | "!=" | "contains" | "startswith" | "endswith" | "~" | "in"
               | "<" | "<=" | ">" | ">="
    value     := string | "i" string | number | "true" | "false" | "null"
               | "[" [value ("," value)*] "]"
    path      := name ("." name)*        (a name is letters, digits and _)

What each part means:

- A path on its own means "is truthy": there, and not false, 0, "", [], {} or null.
- A path through a list means any item: `to.address endswith "@bank.com"` matches when one
  recipient's address does. A condition on a list's items does too (`labels startswith "A"`),
  but `==`, `!=` and `contains` take the list as it is (`labels contains "IMPORTANT"`).
- `contains` is a substring of a text, or an item of a list. `in` is "one of these values".
- `<`, `<=`, `>` and `>=` compare numbers, or times (a value that is an ISO 8601 time compares
  the field's text as a time; one without a zone is UTC).
- `~` searches with a regex (RE2's syntax: linear time, no lookarounds or backreferences), in
  at most the first 64 KiB of the field, since someone else's email is what it reads.
- Strings are JSON's (in double quotes); `i"…"` compares without regard to case.
- `exists`: the field is there, and not null.
- A field the event lacks never matches, except under `not`.

When a subscription is saved, its filter is checked against the fields of every event type it
could get (check): a field they lack, or an operator on the wrong kind of value, is refused
(InvalidFilter), listing the type's fields. shorthand() writes a filter with the fewest
parentheses that keep its meaning, and parse(shorthand(f)) == f.
"""

# System Imports
import functools
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Final, NoReturn, Protocol, Self, cast

# Package Imports
import re2  # pyright: ignore[reportMissingTypeStubs]
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    Tag,
    ValidationError,
    model_validator,
)

# Local Imports
from udl.automations.events import EventField, EventType, event_fields
from udl.errors import InvalidFilter

MAX_SEARCHED: Final = 64 * 1024
"""Characters of a field a regex reads at most."""

_PATH: Final = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
_WORD: Final = re.compile(r"[A-Za-z0-9_.]+")
_SPACE: Final = re.compile(r"\s*")
_JSON: Final = json.JSONDecoder(parse_constant=lambda name: _refuse(name))


def _refuse(name: str) -> None:
    raise ValueError(f"{name} is not a value")


class Op(StrEnum):
    """What a condition asks of a field."""

    TRUTHY = "truthy"  # (a path on its own)
    EQ = "eq"
    NE = "ne"
    CONTAINS = "contains"
    STARTSWITH = "startswith"
    ENDSWITH = "endswith"
    MATCHES = "matches"
    IN = "in"
    EXISTS = "exists"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"


# How each operator is written in the shorthand.
_SYMBOLS: Final[dict[Op, str]] = {
    Op.EQ: "==",
    Op.NE: "!=",
    Op.CONTAINS: "contains",
    Op.STARTSWITH: "startswith",
    Op.ENDSWITH: "endswith",
    Op.MATCHES: "~",
    Op.IN: "in",
    Op.EXISTS: "exists",
    Op.LT: "<",
    Op.LE: "<=",
    Op.GT: ">",
    Op.GE: ">=",
}
_SIGNS: Final = ("==", "!=", "<=", ">=", "<", ">", "~")  # (the two-character ones first)
_WORDS: Final = {"contains": Op.CONTAINS, "startswith": Op.STARTSWITH, "endswith": Op.ENDSWITH}
_TEXT_OPS: Final = frozenset({Op.STARTSWITH, Op.ENDSWITH, Op.MATCHES})
_ORDER_OPS: Final = frozenset({Op.LT, Op.LE, Op.GT, Op.GE})
# The operators that take a list as it is; the others look at each of its items.
_WHOLE_OPS: Final = frozenset({Op.TRUTHY, Op.EXISTS, Op.EQ, Op.NE, Op.CONTAINS})

type Scalar = str | int | float | bool | None
type FilterValue = Scalar | list[Scalar]


class _Model(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_attribute_docstrings=True,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )


# ---------------------------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------------------------


class Condition(_Model):
    """A condition on one field of an event's data."""

    field: str
    """The field: a dotted path into the event's data, e.g. from.address."""
    op: Op = Op.TRUTHY
    """What it asks: truthy (the field on its own), eq, ne, contains, startswith, endswith,
    matches (a regex), in, exists, lt, le, gt or ge."""
    value: FilterValue = None
    """What the field is compared with (a list, for in; none for truthy and exists)."""
    case_sensitive: bool = True
    """Whether text is compared with regard to case."""

    @model_validator(mode="before")
    @classmethod
    def _case_matters_only_for_text(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        given = cast(dict[str, object], data)
        if not given.get("case_sensitive", True) and not _has_text(given.get("value")):
            return {**given, "case_sensitive": True}
        return given

    @model_validator(mode="after")
    def _fits(self) -> Self:
        problem = _problem(self)
        if problem is not None:
            raise ValueError(problem)
        if self.op is Op.MATCHES:
            _pattern(cast(str, self.value), self.case_sensitive)  # (raises ValueError)
        return self


class AllOf(_Model):
    """Every one of its filters matches."""

    all: Annotated[list[Filter], Field(min_length=2)]
    """The filters (two or more)."""

    @model_validator(mode="before")
    @classmethod
    def _flattened(cls, data: object) -> object:
        return _flatten(data, "all", AllOf)

    @classmethod
    def of(cls, *filters: Filter) -> Self:
        return cls.model_validate({"all": list(filters)})


class AnyOf(_Model):
    """At least one of its filters matches."""

    any: Annotated[list[Filter], Field(min_length=2)]
    """The filters (two or more)."""

    @model_validator(mode="before")
    @classmethod
    def _flattened(cls, data: object) -> object:
        return _flatten(data, "any", AnyOf)

    @classmethod
    def of(cls, *filters: Filter) -> Self:
        return cls.model_validate({"any": list(filters)})


class Not(_Model):
    """Its filter does not match."""

    not_: Filter = Field(alias="not")
    """The filter."""

    @classmethod
    def of(cls, inner: Filter) -> Self:
        return cls.model_validate({"not": inner})


def _kind_of(value: object) -> str | None:
    """Which kind of filter `value` is (for the union)."""
    if isinstance(value, dict):
        given = cast(dict[str, object], value)
        return next((key for key in ("all", "any", "not") if key in given), "condition")
    return _KINDS.get(type(value))


def _one_is_itself(value: object) -> object:
    """An all or any of one filter is that filter."""
    found = value
    while isinstance(found, dict):
        given = cast(dict[str, object], found)
        if len(given) != 1:
            break
        [(key, items)] = given.items()
        if key not in ("all", "any") or not isinstance(items, list):
            break
        if len(cast(list[object], items)) != 1:
            break
        found = cast(list[object], items)[0]
    return cast(object, found)


type Filter = Annotated[
    Annotated[Condition, Tag("condition")]
    | Annotated[AllOf, Tag("all")]
    | Annotated[AnyOf, Tag("any")]
    | Annotated[Not, Tag("not")],
    Discriminator(_kind_of),
    BeforeValidator(_one_is_itself),
]
"""A filter: a condition, or all, any or not of filters."""

_KINDS: Final[dict[type, str]] = {Condition: "condition", AllOf: "all", AnyOf: "any", Not: "not"}
for _model in (AllOf, AnyOf, Not):
    _model.model_rebuild()


def _from_text(value: object) -> object:
    """A filter written as the shorthand, as its tree."""
    if not isinstance(value, str):
        return value
    try:
        return parse(value)
    except InvalidFilter as e:
        raise ValueError(e.message) from e


type FilterField = Annotated[
    Filter, BeforeValidator(_from_text, json_schema_input_type=str | Filter)
]
"""A filter, as its tree or as the shorthand."""


def _flatten(data: object, key: str, cls: type[BaseModel]) -> object:
    """An all (or any) whose filters' alls (anys) are its own."""
    if not isinstance(data, dict):
        return data
    given = cast(dict[str, object], data)
    if not isinstance(given.get(key), list):
        return given
    flat: list[object] = []
    waiting: list[object] = list(reversed(cast(list[object], given[key])))
    while waiting:
        item = _one_is_itself(waiting.pop())
        if isinstance(item, cls):
            waiting.extend(reversed(cast(list[object], getattr(item, key))))
        elif isinstance(item, dict) and set(cast(dict[str, object], item)) == {key}:
            waiting.extend(reversed(cast(dict[str, list[object]], item)[key]))
        else:
            flat.append(cast(object, item))
    return {**given, key: flat}


def _has_text(value: Any) -> bool:
    return isinstance(value, str) or (
        isinstance(value, list) and any(isinstance(item, str) for item in cast(list[Any], value))
    )


def _problem(condition: Condition) -> str | None:
    """What is wrong with a condition, whatever its field, if anything."""
    field, op, value = condition.field, condition.op, condition.value
    if not _PATH.fullmatch(field) or field.split(".")[0] == "not":
        return (
            f"{field!r} is not a field: a field is dotted names of letters, digits and _ "
            "(e.g. from.address), and not starting with not"
        )
    values = cast(list[Any], value) if isinstance(value, list) else [value]
    if any((isinstance(item, float) and item != item) or item in (_INF, -_INF) for item in values):
        return "numbers in a filter are finite"
    symbol = _SYMBOLS.get(op, "a field on its own")
    if op in (Op.TRUTHY, Op.EXISTS):
        return None if value is None else f"{symbol} takes no value"
    if op is Op.IN:
        return None if isinstance(value, list) else f"{symbol} takes a list of values"
    if isinstance(value, list) and op is not Op.EQ and op is not Op.NE:
        return f"{symbol} takes one value, not a list"
    if op in _TEXT_OPS and not isinstance(value, str):
        return f"{symbol} takes text, not {json.dumps(value)}"
    if op in _ORDER_OPS:
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            return f"{symbol} takes a number or a time, not {json.dumps(value)}"
        if isinstance(value, str) and _time(value) is None:
            return f"{symbol} takes a number or a time (ISO 8601), not {json.dumps(value)}"
    return None


_INF: Final = float("inf")


class _Regex(Protocol):
    def search(self, text: str, /) -> object | None: ...


# (google-re2 has no type stubs.)
_RE2: Final[Any] = re2
_RE2_ERROR: Final = cast(type[Exception], _RE2.error)


@functools.lru_cache(maxsize=512)
def _pattern(source: str, case_sensitive: bool) -> _Regex:
    """`source` compiled by RE2. Raises ValueError when RE2 cannot run it."""
    options = _RE2.Options()
    options.log_errors = False
    options.case_sensitive = case_sensitive
    try:
        return cast(_Regex, _RE2.compile(source, options=options))
    except _RE2_ERROR as e:
        why = e.args[0].decode() if e.args and isinstance(e.args[0], bytes) else str(e)
        raise ValueError(f"{json.dumps(source)} is not a regex RE2 can run: {why}") from e


def _time(text: str) -> datetime | None:
    """`text` as a time (UTC when it says no zone), or None when it is not one."""
    try:
        found = datetime.fromisoformat(text)
    except ValueError:
        return None
    return found if found.tzinfo is not None else found.replace(tzinfo=UTC)


def conditions(tree: Filter) -> Iterator[Condition]:
    """The conditions in a filter, in order."""
    match tree:
        case Condition():
            yield tree
        case Not():
            yield from conditions(tree.not_)
        case AllOf():
            for child in tree.all:
                yield from conditions(child)
        case _:  # (any)
            for child in tree.any:
                yield from conditions(child)


# ---------------------------------------------------------------------------------------------
# The shorthand
# ---------------------------------------------------------------------------------------------


def parse(text: str) -> Filter:
    """The filter `text` (the shorthand) writes. Raises InvalidFilter, saying where."""
    return _Parser(text).parse()


def shorthand(tree: Filter) -> str:
    """A filter in the shorthand, with the fewest parentheses that keep its meaning."""
    match tree:
        case Condition():
            return _condition_text(tree)
        case Not():
            inner = shorthand(tree.not_)
            plain = isinstance(tree.not_, Condition | Not)
            return f"not {inner}" if plain else f"not ({inner})"
        case AllOf():
            return " and ".join(
                f"({shorthand(child)})" if isinstance(child, AnyOf) else shorthand(child)
                for child in tree.all
            )
        case _:  # (any)
            return " or ".join(shorthand(child) for child in tree.any)


def _condition_text(condition: Condition) -> str:
    if condition.op is Op.TRUTHY:
        return condition.field
    if condition.op is Op.EXISTS:
        return f"{condition.field} exists"
    value = _value_text(condition.value, condition.case_sensitive)
    return f"{condition.field} {_SYMBOLS[condition.op]} {value}"


def _value_text(value: FilterValue, case_sensitive: bool) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_value_text(item, case_sensitive) for item in value) + "]"
    written = json.dumps(value, ensure_ascii=False)
    return written if case_sensitive or not isinstance(value, str) else f"i{written}"


class _Parser:
    """Reads the shorthand (a recursive descent, saying where it fails)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.at = 0

    def parse(self) -> Filter:
        found = self._or()
        self._space()
        if self.at < len(self.text):
            self._fail(f"unexpected {self.text[self.at]!r}")
        return found

    def _fail(self, problem: str, at: int | None = None) -> NoReturn:
        where = (self.at if at is None else at) + 1
        raise InvalidFilter(
            f"The filter {self.text!r} does not parse: {problem} at {where}",
            hint='e.g. from.address endswith "@bank.com" and not sent_by_udl: fields, '
            "operators (== != contains startswith endswith ~ in < <= > >= exists), values "
            '("text", i"text", numbers, true, false, null, [lists]), and, or, not, ( ).',
        )

    def _space(self) -> None:
        found = _SPACE.match(self.text, self.at)
        self.at = found.end() if found else self.at

    def _word(self) -> str | None:
        """The word at the reading position (without reading it)."""
        self._space()
        found = _WORD.match(self.text, self.at)
        return found.group() if found else None

    def _take(self, word: str) -> bool:
        """Read `word` when it is the next one."""
        if self._word() != word:
            return False
        self.at += len(word)
        return True

    def _or(self) -> Filter:
        found = [self._and()]
        while self._take("or"):
            found.append(self._and())
        return found[0] if len(found) == 1 else AnyOf.model_validate({"any": found})

    def _and(self) -> Filter:
        found = [self._unary()]
        while self._take("and"):
            found.append(self._unary())
        return found[0] if len(found) == 1 else AllOf.model_validate({"all": found})

    def _unary(self) -> Filter:
        self._space()
        if self.text.startswith("(", self.at):
            self.at += 1
            inner = self._or()
            self._space()
            if not self.text.startswith(")", self.at):
                self._fail("expected )")
            self.at += 1
            return inner
        if self._take("not"):
            return Not.model_validate({"not": self._unary()})
        return self._condition()

    def _condition(self) -> Condition:
        start = self.at
        path = self._word()
        if path is None:
            self._fail("expected a field")
        self.at += len(path)
        if not _PATH.fullmatch(path) or path.split(".")[0] == "not":
            self._fail(
                f"{path!r} is not a field (a field is dotted names of letters, digits and _)",
                at=start,
            )
        op = self._op()
        if op is None or op is Op.EXISTS:
            return self._made({"field": path, "op": op or Op.TRUTHY}, at=start)
        self._space()
        value_at = self.at
        value, case_sensitive = self._value()
        return self._made(
            {"field": path, "op": op, "value": value, "case_sensitive": case_sensitive},
            at=value_at,
        )

    def _made(self, fields: dict[str, Any], *, at: int) -> Condition:
        try:
            return Condition.model_validate(fields)
        except ValidationError as e:
            problems = (
                problem.get("ctx", {}).get("error", problem["msg"]) for problem in e.errors()
            )
            self._fail("; ".join(str(problem) for problem in problems), at=at)

    def _op(self) -> Op | None:
        """The operator after a field, if there is one (None: the field on its own)."""
        self._space()
        for sign in _SIGNS:
            if self.text.startswith(sign, self.at):
                self.at += len(sign)
                return next(op for op, written in _SYMBOLS.items() if written == sign)
        word = self._word()
        if word is None or word in ("and", "or"):
            if word is None and self.at < len(self.text) and self.text[self.at] != ")":
                self._fail(f"unexpected {self.text[self.at]!r}")
            return None
        found = {**_WORDS, "in": Op.IN, "exists": Op.EXISTS}.get(word)
        if found is None:
            self._fail(f"unexpected {word!r}")
        self.at += len(word)
        return found

    def _value(self) -> tuple[FilterValue, bool]:
        """A value, and whether its text is compared with regard to case."""
        if not self.text.startswith("[", self.at):
            return self._scalar()
        start = self.at
        self.at += 1
        items: list[Scalar] = []
        cases: set[bool] = set()
        self._space()
        if self.text.startswith("]", self.at):
            self.at += 1
            return items, True
        while True:
            self._space()
            item, case_sensitive = self._scalar()
            items.append(item)
            if isinstance(item, str):
                cases.add(case_sensitive)
            self._space()
            if self.text.startswith(",", self.at):
                self.at += 1
                continue
            if self.text.startswith("]", self.at):
                self.at += 1
                break
            self._fail("expected , or ]")
        if len(cases) > 1:
            self._fail('i"…" is for every text in a list, or for none of it', at=start)
        return items, cases != {False}

    def _scalar(self) -> tuple[Scalar, bool]:
        self._space()
        start = self.at
        case_sensitive = True
        if self.text.startswith('i"', self.at):
            case_sensitive = False
            self.at += 1
        if self.at >= len(self.text) or self.text[self.at] in "[{":
            self._fail("expected a value (text, a number, true, false or null)", at=start)
        try:
            value, end = _JSON.raw_decode(self.text, self.at)
        except ValueError:
            if self.text[self.at] == '"':
                self._fail('the string here is not closed (it ends with a ")', at=self.at)
            self._fail(
                "expected a value (text, in double quotes, a number, true, false or null)",
                at=start,
            )
        self.at = end
        return cast(Scalar, value), case_sensitive


# ---------------------------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------------------------


def matches(tree: Filter, data: Mapping[str, JsonValue]) -> bool:
    """Whether an event's data matches a filter."""
    match tree:
        case Condition():
            return _matches(tree, data)
        case Not():
            return not matches(tree.not_, data)
        case AllOf():
            return all(matches(child, data) for child in tree.all)
        case _:  # (any)
            return any(matches(child, data) for child in tree.any)


def failing(tree: Filter, data: Mapping[str, JsonValue]) -> str | None:
    """The part of a filter that an event's data fails (in the shorthand), or None when it
    matches: in an all, the first of its filters that fails (and the part of it that does)."""
    if matches(tree, data):
        return None
    if isinstance(tree, AllOf):
        return next(found for child in tree.all if (found := failing(child, data)) is not None)
    return shorthand(tree)


def _matches(condition: Condition, data: Mapping[str, JsonValue]) -> bool:
    found = _found(data, condition.field.split("."))
    if condition.op not in _WHOLE_OPS:
        found = [item for value in found for item in _items(value)]
    return any(_test(condition, value) for value in found)


def _found(data: Mapping[str, JsonValue], parts: list[str]) -> list[JsonValue]:
    """The values at a path in `data` (any of a list's items, on the way)."""
    found: list[JsonValue] = [cast(JsonValue, dict(data))]
    for n, part in enumerate(parts):
        here = [
            cast(dict[str, JsonValue], value)[part]
            for value in found
            if isinstance(value, dict) and part in value
        ]
        last = n == len(parts) - 1
        found = here if last else [item for value in here for item in _items(value)]
    return found


def _items(value: JsonValue) -> list[JsonValue]:
    """A value's items, when it is a list (lists in it too), else the value itself."""
    if not isinstance(value, list):
        return [value]
    return [item for inner in value for item in _items(inner)]


def _test(condition: Condition, value: JsonValue) -> bool:
    op, target, case_sensitive = condition.op, condition.value, condition.case_sensitive
    match op:
        case Op.TRUTHY:
            return bool(value)
        case Op.EQ:
            return _same(value, target, case_sensitive)
        case Op.NE:
            return not _same(value, target, case_sensitive)
        case Op.IN:
            options = cast(list[Scalar], target)
            return any(_same(value, option, case_sensitive) for option in options)
        case Op.CONTAINS:
            if isinstance(value, str) and isinstance(target, str):
                return _folded(target, case_sensitive) in _folded(value, case_sensitive)
            if isinstance(value, list):
                return any(_same(item, target, case_sensitive) for item in value)
            return False
        case Op.STARTSWITH | Op.ENDSWITH:
            if not isinstance(value, str):
                return False
            text, part = _folded(value, case_sensitive), _folded(cast(str, target), case_sensitive)
            return text.startswith(part) if op is Op.STARTSWITH else text.endswith(part)
        case Op.MATCHES:
            pattern = _pattern(cast(str, target), case_sensitive)
            return isinstance(value, str) and pattern.search(value[:MAX_SEARCHED]) is not None
        case Op.EXISTS:
            return value is not None
        case _:  # (<, <=, >, >=)
            return _ordered(op, value, cast(Scalar, target))


def _folded(text: str, case_sensitive: bool) -> str:
    return text if case_sensitive else text.casefold()


def _same(value: JsonValue, target: FilterValue, case_sensitive: bool) -> bool:
    if isinstance(value, bool) or isinstance(target, bool):
        return type(value) is type(target) and value == target
    if isinstance(value, str) and isinstance(target, str):
        return _folded(value, case_sensitive) == _folded(target, case_sensitive)
    if isinstance(value, list) and isinstance(target, list):
        return len(value) == len(target) and all(
            _same(item, other, case_sensitive) for item, other in zip(value, target, strict=True)
        )
    if isinstance(value, list | dict) or isinstance(target, list):
        return False
    return value == target


def _ordered(op: Op, value: JsonValue, target: Scalar) -> bool:
    if isinstance(target, str):  # (a time)
        when, than = _time(value) if isinstance(value, str) else None, _time(target)
        if when is None or than is None:
            return False
        left, right = when.timestamp(), than.timestamp()
    elif isinstance(value, int | float) and not isinstance(value, bool):
        left, right = float(value), float(cast(float, target))
    else:
        return False
    match op:
        case Op.LT:
            return left < right
        case Op.LE:
            return left <= right
        case Op.GT:
            return left > right
        case _:
            return left >= right


# ---------------------------------------------------------------------------------------------
# Checking a filter against its event types
# ---------------------------------------------------------------------------------------------


def check(tree: Filter, types: Sequence[EventType[Any]]) -> None:
    """Raise InvalidFilter when a filter names a field one of `types` lacks (custom types have
    any), or asks of a field what it cannot be."""
    for event_type in types:
        if event_type.is_custom:
            continue
        fields = _fields_of(event_type.data)
        for condition in conditions(tree):
            _check(condition, event_type, fields)


@functools.cache
def _fields_of(model: type[BaseModel]) -> dict[str, EventField]:
    return {found.path: found for found in event_fields(model)}


def _check(condition: Condition, event_type: EventType[Any], fields: dict[str, EventField]) -> None:
    field = fields.get(condition.field)
    if field is None:
        parts = condition.field.split(".")
        prefixes = (".".join(parts[:n]) for n in range(1, len(parts)))
        if any(prefix in fields and fields[prefix].open for prefix in prefixes):
            return  # (under a field that holds anything)
        raise InvalidFilter(
            f"{event_type.name} events have no field {condition.field!r}",
            hint=f"Its fields: {', '.join(fields)}.",
        )
    problem = _misfit(condition, field)
    if problem is not None:
        raise InvalidFilter(
            f"In {event_type.name} events, {condition.field} is {_kind(field)}: {problem}",
            hint=f"`udl events types {event_type.name}` describes its fields.",
        )


def _kind(field: EventField) -> str:
    if field.type == "array":
        return f"a list of {field.items or 'values'}"
    if field.format == "date-time":
        return "a time"
    return {"string": "text", "integer": "a number", "number": "a number"}.get(
        field.type, field.type
    )


def _misfit(condition: Condition, field: EventField) -> str | None:
    """What a condition asks that its field cannot be, if anything."""
    op, value = condition.op, condition.value
    if op in (Op.TRUTHY, Op.EXISTS) or field.open:
        return None
    # The JSON types the value is compared with: the field's, and a list's items'.
    kinds = {field.type} if field.type != "array" else {"array", field.items or "any"}
    symbol = _SYMBOLS[op]
    if op in (Op.EQ, Op.NE, Op.IN):
        values = cast(list[Scalar], value) if op is Op.IN else [value]
        for item in values:
            if not any(_fits(item, kind) for kind in kinds):
                return f"it is never {json.dumps(item)}"
            if field.choices is not None and isinstance(item, str):
                choices = [str(choice) for choice in field.choices]
                folded = [_folded(choice, condition.case_sensitive) for choice in choices]
                if _folded(item, condition.case_sensitive) not in folded:
                    return f"{json.dumps(item)} is not one of {', '.join(choices)}"
        return None
    if op is Op.CONTAINS:
        if "string" in kinds and field.type == "string" and isinstance(value, str):
            return None
        if field.type == "array" and _fits(value, field.items or "any"):
            return None
        return f"{symbol} looks for text in text, or an item in a list"
    if op in _TEXT_OPS:
        return None if "string" in kinds else f"{symbol} reads text"
    # (<, <=, >, >=)
    if isinstance(value, str):
        times = field.format == "date-time" and "string" in kinds
        return None if times else f"{symbol} compares it with a time"
    return None if kinds & {"integer", "number"} else f"{symbol} compares it with a number"


def _fits(value: Scalar | list[Scalar], kind: str) -> bool:
    """Whether `value` can be a field of the JSON type `kind`."""
    if value is None or kind in ("any", "null"):
        return True
    match kind:
        case "string":
            return isinstance(value, str)
        case "boolean":
            return isinstance(value, bool)
        case "integer" | "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        case "array":
            return isinstance(value, list)
        case _:
            return False
