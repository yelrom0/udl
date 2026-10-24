"""
----------------
- templates.py -
----------------

Templates: what an action's arguments and an AI's prompts are written in (Jinja2), rendered with
the event that set them off. That event was written by anyone (an email, a Slack message), so a
template runs in a sandbox:

- Jinja's immutable sandbox: nothing private (no `_` names), nothing changed in place, no
  unsafe calls, and no loader, so no file is ever read, included or imported;
- StrictUndefined: a name the event does not have fails, saying the template's line, rather than
  rendering as nothing;
- limits (Limits): what a rendering gives, and any text or list it makes on the way, is at most
  `output` long (repeating, joining, replacing, padding and formatting text are measured before
  they are done), numbers stay a sensible size, and loops run at most `steps` times in all.

No autoescaping: what templates make is not HTML. A value that is a single `{{ expression }}`
keeps its type (render_value), so `{"urls": "{{ event.data.body_text | urls }}"}` gives a list.
The filters are Jinja's (but `format`), and `urls` (the links in a text); `tojson` writes plain
JSON.
"""

# System Imports
import functools
import json
import re
import string
import traceback
from collections import deque
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sized
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Final, cast

# Package Imports
from jinja2 import StrictUndefined, TemplateSyntaxError, Undefined, nodes
from jinja2.exceptions import SecurityError
from jinja2.filters import make_attrgetter
from jinja2.parser import Parser
from jinja2.sandbox import ImmutableSandboxedEnvironment
from jinja2.visitor import NodeTransformer
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError, to_jsonable_python

# Local Imports
from udl.errors import TemplateError

MAX_OUTPUT: Final = 256 * 1024
"""Characters a rendering gives at most (and the longest text or list it makes on the way)."""
MAX_STEPS: Final = 100_000
"""Loop iterations a rendering takes at most, in all."""
_MAX_BITS: Final = 1 << 16
"""The largest number a rendering makes, in bits (about 20,000 digits)."""

# A link in a text: up to a space, a quote or a bracket, without the punctuation after it.
_URL: Final = re.compile(r"https?://[^\s<>\"'()\[\]{}]+")
_TRAILING: Final = ".,;:!?"
# A value that is a single expression: {{ ... }}, with only spaces around it.
_LONE: Final = re.compile(r"\s*\{\{-?(?P<expression>.*?)-?\}\}\s*", re.DOTALL)
# What Jinja names a template in its errors and tracebacks.
_NAME: Final = "<template>"

_JSON: Final = TypeAdapter[JsonValue](JsonValue)


@dataclass(frozen=True)
class Limits:
    """How much a rendering may make, and do."""

    output: int = MAX_OUTPUT
    steps: int = MAX_STEPS


# The loop iterations the rendering in progress has left, of how many (in a list, so loops count
# it down).
_steps: ContextVar[list[int]] = ContextVar("udl_template_steps")


class Templates:
    """Renders templates in a sandbox, within `limits`."""

    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits or Limits()
        self._sandbox = _Sandbox(self.limits)

    def check(self, source: str) -> None:
        """Raise TemplateError when `source` is not a template (as an action checks its options
        when it is saved)."""
        with _errors():
            self._sandbox.parse(source)

    def render(self, source: str, context: Mapping[str, Any]) -> str:
        """`source` rendered with `context`. Raises TemplateError."""
        with _errors(), self._counting():
            template = self._sandbox.from_string(source)
            rendered: list[str] = []
            length = 0
            for chunk in template.generate(context):
                length += len(chunk)
                _fits(length, self.limits, "Its output")
                rendered.append(chunk)
            return "".join(rendered)

    def render_value(self, value: JsonValue, context: Mapping[str, Any]) -> JsonValue:
        """`value` with every text in it rendered: a text that is a single `{{ expression }}`
        becomes the expression's value (as JSON), any other a text. Raises TemplateError."""
        if isinstance(value, str):
            lone = _LONE.fullmatch(value)
            expression = lone["expression"] if lone else None
            if expression is None or any(mark in expression for mark in ("{{", "}}", "{%")):
                return self.render(value, context)
            return self._evaluate(expression, context)
        if isinstance(value, list):
            return [self.render_value(item, context) for item in value]
        if isinstance(value, dict):
            return {key: self.render_value(item, context) for key, item in value.items()}
        return value

    def _evaluate(self, expression: str, context: Mapping[str, Any]) -> JsonValue:
        with _errors(), self._counting():
            found = self._sandbox.expression(expression)(context)
            if isinstance(found, Undefined):
                str(found)  # (raises why it is undefined)
            try:
                value = _JSON.validate_python(to_jsonable_python(found))
            except (PydanticSerializationError, ValidationError) as e:
                raise TemplateError(
                    f"{expression.strip()} gives a {type(found).__name__}, which is not JSON"
                ) from e
            _fits(len(json.dumps(value)), self.limits, "Its value")
            return value

    @contextmanager
    def _counting(self) -> Generator[None]:
        """Count the loops of the rendering in the block."""
        token = _steps.set([self.limits.steps, self.limits.steps])
        try:
            yield
        finally:
            _steps.reset(token)


@contextmanager
def _errors() -> Generator[None]:
    """Raise what goes wrong in a template as a TemplateError that says where."""
    try:
        yield
    except TemplateError:
        raise
    except TemplateSyntaxError as e:
        raise TemplateError(f"line {e.lineno}: {e.message}") from e
    except RecursionError as e:
        raise TemplateError("It calls itself without end") from e
    except Exception as e:  # (anything the template's own code raised)
        where = _line(e)
        message = str(e) if isinstance(e, SecurityError) else f"{type(e).__name__}: {e}"
        raise TemplateError(f"line {where}: {message}" if where else message) from e


def _line(error: BaseException) -> int | None:
    """The template's line where `error` was raised (Jinja puts its lines in the traceback)."""
    lines = [
        frame.lineno
        for frame in traceback.extract_tb(error.__traceback__)
        if frame.filename == _NAME
    ]
    return lines[-1] if lines else None


def _fits(length: int, limits: Limits, what: str) -> None:
    if length > limits.output:
        raise TemplateError(f"{what} would be longer than {limits.output} characters")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# ---------------------------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------------------------


class _Sandbox(ImmutableSandboxedEnvironment):
    """Jinja's immutable sandbox, with udl's limits and filters."""

    intercepted_binops = frozenset({"+", "-", "*", "**", "%", "//"})

    def __init__(self, limits: Limits) -> None:
        super().__init__(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
        self.limits = limits
        del self.globals["lipsum"]  # (it writes as much text as it is asked for)
        filters = cast(dict[str, Callable[..., Any]], self.filters)
        del filters["format"]  # (printf widths; `~` and str.format do the same, measured)
        filters.update(
            urls=_urls,
            tojson=_tojson,
            replace=self._replace,
            join=self._join,
            center=self._padding(filters["center"]),
            indent=self._padding(filters["indent"]),
        )
        for name, found in list(filters.items()):
            filters[name] = self._measured(found)
        cast(dict[str, Any], self.globals).update(_udl_counted=_counted, _udl_concat=self._concat)

    # -- what templates are made of ------------------------------------------------------------

    def _parse(self, source: str, name: str | None, filename: str | None) -> nodes.Template:
        tree = _Measured().visit(super()._parse(source, name, filename))
        tree.set_environment(self)
        return tree

    def expression(self, source: str) -> Callable[[Mapping[str, Any]], Any]:
        """A single expression, as compile_expression makes one, with the limits templates
        have."""
        parser = Parser(self, source, state="variable")
        found = parser.parse_expression()
        if not parser.stream.eos:
            raise TemplateSyntaxError("chunk after expression", parser.stream.current.lineno)
        body = [nodes.Assign(nodes.Name("result", "store"), found, lineno=1)]
        tree = _Measured().visit(nodes.Template(body, lineno=1))
        tree.set_environment(self)
        template = self.from_string(tree)

        def evaluate(context: Mapping[str, Any]) -> Any:
            run = template.new_context(dict(context))
            deque(template.root_render_func(run), maxlen=0)  # (runs it; it writes nothing)
            return run.vars["result"]

        return evaluate

    # -- what the sandbox is asked to do -------------------------------------------------------

    def call_binop(self, context: Any, operator: str, left: Any, right: Any) -> Any:
        if operator == "*":
            for repeated, times in ((left, right), (right, left)):
                if isinstance(repeated, str | list | tuple) and _is_int(times):
                    self._fits(len(cast(Sized, repeated)) * times)
        elif operator == "**" and _is_int(left) and _is_int(right) and right > 0:
            if abs(left).bit_length() * right > _MAX_BITS:
                raise SecurityError("a number that large is not worked out in templates")
        elif operator == "%" and isinstance(left, str):
            raise SecurityError("text is not %-formatted in templates (use ~, or str.format)")
        return self._measure(super().call_binop(context, operator, left, right))

    def call(__self, __context: Any, __obj: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: N805
        text = getattr(__obj, "__self__", None)
        if isinstance(text, str):
            args = __self._text_method(text, getattr(__obj, "__name__", ""), args, kwargs)
        return __self._measure(super().call(__context, __obj, *args, **kwargs))

    def wrap_str_format(self, value: Any) -> Callable[..., str] | None:
        wrapped = super().wrap_str_format(value)
        if wrapped is not None:
            for _, field, spec, _ in string.Formatter().parse(value.__self__):
                if field is not None and spec:
                    self._check_spec(spec)
        return wrapped

    def _text_method(
        self, text: str, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[Any, ...]:
        """Measure what a text's method would make, before it makes it."""
        given = [*args, *kwargs.values()]
        if name in ("ljust", "rjust", "center", "zfill") and given and _is_int(given[0]):
            self._fits(given[0])
        elif name == "expandtabs":
            size = given[0] if given and _is_int(given[0]) else 8
            self._fits(len(text) * max(size, 1))
        elif name == "replace" and len(given) >= 2:
            self._fits(_replaced(text, str(given[0]), str(given[1])))
        elif name == "join" and given:
            items = [str(item) for item in cast(Iterable[Any], given[0])]
            self._fits(len(text) * max(len(items) - 1, 0) + sum(map(len, items)))
            return (items,)
        return args

    def _check_spec(self, spec: str) -> None:
        """Refuse a str.format spec that is nested, or pads beyond the limit."""
        if "{" in spec:
            raise SecurityError("format specs are not nested in templates")
        for number in re.findall(r"\d+", spec):
            if int(number) > self.limits.output:
                raise SecurityError(f"{spec!r} pads beyond {self.limits.output} characters")

    # -- filters -------------------------------------------------------------------------------

    def _replace(self, text: Any, old: Any, new: Any, count: int | None = None) -> str:
        text, old, new = str(text), str(old), str(new)
        self._fits(_replaced(text, old, new))
        return text.replace(old, new, -1 if count is None else count)

    def _join(self, items: Any, separator: Any = "", attribute: str | int | None = None) -> str:
        found = cast(Iterable[Any], items)
        if attribute is not None:
            found = map(make_attrgetter(self, attribute), found)
        texts = [str(item) for item in found]
        separator = str(separator)
        self._fits(len(separator) * max(len(texts) - 1, 0) + sum(map(len, texts)))
        return separator.join(texts)

    def _padding(self, pad: Callable[..., Any]) -> Callable[..., Any]:
        """A filter that pads (center, indent), measured by its width before it pads."""

        @functools.wraps(pad)
        def padded(value: Any, width: Any = 4, *args: Any, **kwargs: Any) -> Any:
            if _is_int(width):
                self._fits((len(str(value)) + width) * (str(value).count("\n") + 1))
            return pad(value, width, *args, **kwargs)

        return padded

    # -- limits --------------------------------------------------------------------------------

    def _fits(self, length: int) -> None:
        if length > self.limits.output:  # (as a SecurityError: the error says the line)
            raise SecurityError(
                f"what it makes would be longer than {self.limits.output} characters"
            )

    def _measure[T](self, made: T) -> T:
        found: object = made
        if isinstance(found, str | list | tuple | dict):
            self._fits(len(cast(Sized, found)))
        elif isinstance(found, int) and found.bit_length() > _MAX_BITS:
            raise SecurityError("a number that large is not worked out in templates")
        return made

    def _measured(self, function: Callable[..., Any]) -> Callable[..., Any]:
        """`function` (a filter), whose answer is measured."""

        @functools.wraps(function)  # (Jinja's marks too, such as pass_context)
        def measured(*args: Any, **kwargs: Any) -> Any:
            return self._measure(function(*args, **kwargs))

        return measured

    def _concat(self, parts: list[Any]) -> str:
        """`~`: the parts as one text, measured as it grows."""
        made: list[str] = []
        length = 0
        for part in parts:
            text = str(part)
            length += len(text)
            self._fits(length)
            made.append(text)
        return "".join(made)


class _Measured(NodeTransformer):
    """Rewrites a template so its loops count against the rendering's steps, and `~` measures
    the text it makes."""

    # (Named as Jinja's NodeTransformer calls them.)
    def visit_For(self, node: nodes.For) -> nodes.Node:  # noqa: N802
        self.generic_visit(node)
        node.iter = _call("_udl_counted", cast(nodes.Expr, node.iter), lineno=node.lineno)
        return node

    def visit_Concat(self, node: nodes.Concat) -> nodes.Node:  # noqa: N802
        self.generic_visit(node)
        return _call("_udl_concat", nodes.List(node.nodes, lineno=node.lineno), lineno=node.lineno)


def _call(name: str, argument: nodes.Expr, *, lineno: int) -> nodes.Call:
    return nodes.Call(
        nodes.Name(name, "load", lineno=lineno), [argument], [], None, None, lineno=lineno
    )


def _counted[T](items: Iterable[T]) -> Iterator[T]:
    """`items`, each counted against the rendering's steps."""
    left = _steps.get()
    for item in items:
        left[0] -= 1
        if left[0] < 0:
            raise SecurityError(f"its loops would run more than {left[1]} times")
        yield item


def _replaced(text: str, old: str, new: str) -> int:
    """How long `text` would be with every `old` replaced by `new`."""
    times = len(text) + 1 if not old else text.count(old)
    return len(text) + times * (len(new) - len(old))


# ---------------------------------------------------------------------------------------------
# udl's filters
# ---------------------------------------------------------------------------------------------


def _urls(text: Any) -> list[str]:
    """The links (http and https) in `text`, in order, each once."""
    if text is None:
        return []
    found = (url.rstrip(_TRAILING) for url in _URL.findall(str(text)))
    return list(dict.fromkeys(found))


def _tojson(value: Any, indent: int | None = None) -> str:
    """`value` as JSON (dates and times in ISO 8601), not escaped for HTML."""
    return json.dumps(to_jsonable_python(value), ensure_ascii=False, indent=indent)
