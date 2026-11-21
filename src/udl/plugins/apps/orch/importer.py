"""
------------------
- importer.py -
------------------

A plan file, read as features and tasks (26.7): its tracker, its cards, and the sections that
describe them. `parse_plan` is pure (it reads no records and writes nothing) and `merge` puts
several files' readings together; `Orch.import_plan` is what puts them in udl.

The formats are the ones udl's own plan files are written in:

- a tracker under `## Progress tracker`, whose `- [ ] **Phase N: Title**` line is a feature and
  whose children (`- [ ] N.M …`) are its tasks;
- a single-phase file, whose `# … Phase N: Title` heading is the feature and whose tracker's
  top-level lines are its tasks;
- a card, `**N.M Title** · needs A, B`, whose bullets are a task's spec and whose `needs` are its
  own;
- the sections of a phase (`## Phase N …`) as the feature's spec, and a file's other sections
  (Decisions, Concepts, Spec…) as the plan's own (the feature keyed `plan`), or as the feature's
  in a single-phase file.

A line it cannot read is reported with its line number, never dropped: every `- [` line of a
tracker is either a feature, a task, or an `UnplacedLine`.
"""

# System Imports
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

# Package Imports
from pydantic import BaseModel, ConfigDict, Field

# Local Imports
from udl.errors import ConfigError, DoesNotExist
from udl.plugins.apps.orch.models import (
    BLOCKED,
    DONE,
    MERGED,
    MOVED,
    OPEN,
    READY,
    State,
    UnplacedLine,
)

PLAN_KEY: Final = "plan"
"""The feature a multi-phase file's own sections are: its Decisions, its Concepts, its Spec."""

_STATES: Final[dict[str, State]] = {
    " ": OPEN,
    "x": DONE,
    "~": MERGED,
    "r": READY,
    "!": BLOCKED,
    "m": MOVED,
}
"""What a tracker's mark says a task is: the plan's marks, as states."""

_MARKDOWN: Final = (".md", ".markdown")
"""The files a plan is in: its sections become a record's spec, so it is not any file."""

_TRACKER: Final = "## Progress tracker"
"""The heading a tracker is under."""

_PHASE_LINE: Final = re.compile(
    r"^- \[(?P<mark>.)\] \*\*Phase (?P<key>[0-9A-Za-z.]+):\s*(?P<title>.+?)\*\*(?P<rest>.*)$"
)
"""A phase's line in a tracker: `- [ ] **Phase 26: Work tracking**`."""

_TASK_LINE: Final = re.compile(
    r"^ {2}- \[(?P<mark>.)\] (?P<key>[0-9]+(?:\.[0-9]+[a-z]?)?)\s+(?P<title>.+)$"
)
"""A task's line in a tracker: `  - [ ] 26.6 Operations`."""

_TOP_LINE: Final = re.compile(
    r"^- \[(?P<mark>.)\] (?P<key>[0-9]+(?:\.[0-9]+[a-z]?)?)\s+(?P<title>.+)$"
)
"""A single-phase file's top-level line: `- [ ] 22.1 **Core** …`."""

_BOLD_LINE: Final = re.compile(
    r"^- \[(?P<mark>.)\] \*\*(?P<key>[0-9]+(?:\.[0-9]+[a-z]?)?)\*\*\s*(?P<title>.*)$"
)
"""A single-phase file's top-level line, its key in bold: `- [ ] **22.0** Workspace:`."""

_PHASE_HEADING: Final = re.compile(
    r"^#{1,2} (?:UDL )?Phase (?P<key>[0-9]+)(?::|\s)\s*"
    r"(?P<title>[^(]+?)(?:\s*\(`?(?P<plugin>[a-z_]+)`?\))?$"
)
"""A phase's own heading: `## Phase 8: Groundwork`, `# UDL Phase 22: Proxmox plugin`."""

_CARD: Final = re.compile(
    r"^\*\*(?P<key>[0-9]+\.[0-9]+[a-z]?)\s+(?P<title>[^*]+?)\*\*\s*(?:·\s*(?P<rest>.*))?$"
)
"""A card's line: `**26.7 Markdown import** · needs 26.6`."""

_NEEDS: Final = re.compile(r"needs\s+(?P<needs>[^·]+)")
"""What a card's line says it needs: `needs 26.2\u201326.5`."""

_RANGE: Final = re.compile(
    r"(?P<first>[0-9]+(?:\.[0-9]+)?)\s*[\u2013-]\s*(?P<last>[0-9]+(?:\.[0-9]+)?)"
)
"""A range of keys: `17.5\u201317.7`, or with a hyphen."""

_HEADING: Final = re.compile(r"^(?P<level>#{1,2}) (?P<text>.+)$")
"""A section's heading, of the two levels that say where a section ends."""

_ANY_HEADING: Final = re.compile(r"^#{1,3} ")
"""A heading of any of the three levels: what ends a tracker's own section."""

_BULLET: Final = re.compile(r"^\s*- (?P<text>.*)$")
"""A bullet of a card, at any depth."""


@dataclass
class _Task:
    """A task being read: mutable, as a file's lines arrive one at a time."""

    key: str
    title: str
    state: State = OPEN
    blocked_on: str | None = None
    spec: list[str] = field(default_factory=list[str])
    needs: list[str] = field(default_factory=list[str])


@dataclass
class _Feature:
    """A feature being read: its own words, and the tasks of it."""

    key: str
    title: str = ""
    spec: list[str] = field(default_factory=list[str])
    tasks: dict[str, _Task] = field(default_factory=dict[str, _Task])


class ParsedTask(BaseModel):
    """A task, as a plan file has it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    title: str
    state: State = OPEN
    blocked_on: str | None = None
    spec: str = ""
    needs: list[str] = Field(default_factory=list)


class ParsedFeature(BaseModel):
    """A feature, as a plan file has it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    title: str
    spec: str = ""
    tasks: list[ParsedTask] = Field(default_factory=list[ParsedTask])


class ParsedPlan(BaseModel):
    """What a plan file says: its features and tasks, and the lines it could not place."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    features: list[ParsedFeature] = Field(default_factory=list[ParsedFeature])
    unplaced: list[UnplacedLine] = Field(default_factory=list[UnplacedLine])


def parse_plan(text: str, *, source: str) -> ParsedPlan:
    """`text` (a plan file's, named `source`) as features and tasks, and the lines of its tracker
    it could not place."""
    lines = text.splitlines()
    features: dict[str, _Feature] = {}
    unplaced: list[UnplacedLine] = []

    def feature(key: str, title: str = "") -> _Feature:
        if key not in features:
            features[key] = _Feature(key=key)
        found = features[key]
        if title and not found.title:
            found.title = title
        return found

    def task(feature_key: str, key: str, title: str) -> _Task:
        found = feature(feature_key)
        if key not in found.tasks:
            found.tasks[key] = _Task(key=key, title=title)
        elif title and not found.tasks[key].title:
            found.tasks[key].title = title
        return found.tasks[key]

    single = _single_phase(lines)
    tracker = _tracker(lines)
    if tracker is not None:
        _read_tracker(lines, tracker, single, feature, task, unplaced, source)
    _read_sections(lines, single, feature)
    _read_cards(lines, feature, task)
    return _as_plan(features, unplaced)


def merge(*plans: ParsedPlan) -> ParsedPlan:
    """The plans as one: a feature or a task by its key, what one file names filled in by the
    next (the tracker's titles and marks, the cards' specs and needs)."""
    features: dict[str, _Feature] = {}
    unplaced: list[UnplacedLine] = []
    for plan in plans:
        for parsed in plan.features:
            found = features.setdefault(parsed.key, _Feature(key=parsed.key))
            if not found.title:
                found.title = parsed.title
            if parsed.spec:
                found.spec.append(parsed.spec)
            for one in parsed.tasks:
                held = found.tasks.setdefault(one.key, _Task(key=one.key, title=one.title))
                if not held.title:
                    held.title = one.title
                if held.state == OPEN and one.state != OPEN:
                    held.state = one.state
                if held.blocked_on is None:
                    held.blocked_on = one.blocked_on
                if one.spec:
                    held.spec.append(one.spec)
                held.needs = _unique([*held.needs, *one.needs])
        unplaced.extend(plan.unplaced)
    return _as_plan(features, unplaced)


# ---------------------------------------------------------------------------------------------
# Reading the lines
# ---------------------------------------------------------------------------------------------


def _as_plan(features: dict[str, _Feature], unplaced: list[UnplacedLine]) -> ParsedPlan:
    """The features being read, as the plan they say they are."""
    return ParsedPlan(
        features=[
            ParsedFeature(
                key=found.key,
                title=found.title,
                spec="\n\n".join(found.spec),
                tasks=[
                    ParsedTask(
                        key=one.key,
                        title=one.title,
                        state=one.state,
                        blocked_on=one.blocked_on,
                        spec="\n".join(one.spec),
                        needs=one.needs,
                    )
                    for one in found.tasks.values()
                ],
            )
            for _, found in sorted(features.items(), key=lambda pair: _sorted_key(pair[0]))
        ],
        unplaced=unplaced,
    )


def _single_phase(lines: list[str]) -> str | None:
    """The key of the phase a single-phase file's own heading names (a level-1 heading, before
    any level-2 one), or None for a file of several phases."""
    for line in lines:
        if _HEADING.match(line):
            found = _PHASE_HEADING.match(line)
            if line.startswith("# "):
                return found["key"] if found else None
            return None
    return None


def _tracker(lines: list[str]) -> tuple[int, int] | None:
    """Where the tracker is: the line after its heading, and the line its section ends at."""
    for index, line in enumerate(lines):
        if line.strip() != _TRACKER:
            continue
        for end in range(index + 1, len(lines)):
            if _HEADING.match(lines[end]):
                return index + 1, end
        return index + 1, len(lines)
    return None


def _read_tracker(
    lines: list[str],
    tracker: tuple[int, int],
    single: str | None,
    feature: _Named,
    task: _Tasked,
    unplaced: list[UnplacedLine],
    source: str,
) -> None:
    """The tracker's lines: a phase (a feature) and its children (its tasks), in a file of
    several phases; in a single-phase file, its top-level lines are its tasks."""
    start, end = tracker
    phase: str | None = None
    last: _Task | None = None
    for index in range(start, end):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            last = None
            continue
        found = _PHASE_LINE.match(raw)
        if found:
            state = _state(found["mark"], unplaced, index, raw, source)
            # (A phase it cannot read is not the last one it could: its children are nobody's.)
            phase = found["key"] if state is not None else None
            if phase is not None:
                feature(phase, _titled(found["title"]))
            last = None
            continue
        found = _TASK_LINE.match(raw) or (_TOP_LINE.match(raw) if single else None)
        if found:
            state = _state(found["mark"], unplaced, index, raw, source)
            owner = single or phase
            if state is None:
                last = None
                continue
            if owner is None:
                unplaced.append(_unplaced(source, index, raw, "a task before any phase"))
                last = None
                continue
            last = task(owner, found["key"], _titled(found["title"]))
            last.state = state
            if state == BLOCKED:
                last.blocked_on = _after_blocked(stripped)
            continue
        found = _BOLD_LINE.match(raw) if single else None
        if found:
            state = _state(found["mark"], unplaced, index, raw, source)
            if state is None:
                last = None
                continue
            last = task(single or "", found["key"], _titled(found["title"]))
            last.state = state
            if state == BLOCKED:
                last.blocked_on = _after_blocked(stripped)
            continue
        if stripped.startswith("- ["):
            unplaced.append(_unplaced(source, index, raw, "not a task's line: no key"))
            last = None
            continue
        bullet = _BULLET.match(raw)
        if bullet and last is not None:
            last.spec.append(bullet["text"].strip())
            continue
        last = None


def _read_sections(lines: list[str], single: str | None, feature: _Named) -> None:
    """The sections of the file: a phase's own (`## Phase N …`) are its spec, and what is not a
    phase's is the plan's (or, in a single-phase file, the phase's own)."""
    heading: str | None = None
    body: list[str] = []
    for raw in [*lines, ""]:
        found = _HEADING.match(raw)
        if found:
            _kept_heading(heading, body, single, feature)
            heading, body = raw, []
            continue
        if heading is not None:
            body.append(raw)
    _kept_heading(heading, body, single, feature)


def _kept_heading(
    heading: str | None, body: list[str], single: str | None, feature: _Named
) -> None:
    """What a section says goes where its heading says it goes."""
    if heading is None:
        return
    if heading.strip() == _TRACKER:
        # (The tracker's entries are its features and tasks; what else the section says — "Next
        # to build", the marks — is the plan's own words.)
        prose: list[str] = []
        for line in body:
            if _ANY_HEADING.match(line):
                break
            if line.strip().startswith("-"):
                continue
            prose.append(line)
        said = "\n".join(prose).strip()
        if said:
            # (In a single-phase file everything is the phase's, its hand check included.)
            if single is not None:
                feature(single).spec.append(said)
            else:
                feature(PLAN_KEY, "The plan").spec.append(said)
        return
    text = "\n".join(body).strip()
    found = _PHASE_HEADING.match(heading)
    if found:
        if text:
            feature(found["key"], _titled(found["title"])).spec.append(text)
        return
    if not text:
        return
    if single is not None:
        feature(single).spec.append(text)
        return
    feature(PLAN_KEY, "The plan").spec.append(text)


def _read_cards(lines: list[str], feature: _Named, task: _Tasked) -> None:
    """A card's line, and the bullets under it, as a task: its title, its needs and its spec."""
    phase: str | None = None
    for index, raw in enumerate(lines):
        if _HEADING.match(raw):
            found_phase = _PHASE_HEADING.match(raw)
            phase = found_phase["key"] if found_phase else None
            continue
        found = _CARD.match(raw)
        if not found:
            continue
        key = found["key"]
        owner = phase or key.split(".", 1)[0]
        made = task(owner, key, _titled(found["title"]))
        made.needs = _needs(found["rest"] or "")
        made.spec.extend(_bullets(lines, index + 1))


def _bullets(lines: list[str], start: int) -> list[str]:
    """The bullets a card has, from `start` to the next heading, card or section's end."""
    said: list[str] = []
    for raw in lines[start:]:
        if _HEADING.match(raw) or _CARD.match(raw):
            break
        found = _BULLET.match(raw)
        if found:
            said.append(found["text"].strip())
        elif not raw.strip():
            continue
        elif said:
            said[-1] = f"{said[-1]}\n{raw.strip()}"
    return said


# ---------------------------------------------------------------------------------------------
# What a line says
# ---------------------------------------------------------------------------------------------


def _state(
    mark: str, unplaced: list[UnplacedLine], index: int, raw: str, source: str
) -> State | None:
    """The state `mark` says, or None (and the line reported) when it is not a mark the plan has."""
    if mark in _STATES:
        return _STATES[mark]
    unplaced.append(_unplaced(source, index, raw, f"not a mark I know: {mark!r}"))
    return None


def _unplaced(source: str, index: int, raw: str, why: str) -> UnplacedLine:
    """The line at `index` (from 0), reported."""
    return UnplacedLine(source=source, line=index + 1, text=raw, why=why)


def _after_blocked(text: str) -> str | None:
    """What a `[!]` line waits on: the text after `BLOCKED:`."""
    found = re.search(r"BLOCKED:\s*(?P<why>.+)$", text)
    return found["why"].strip() if found else None


def _needs(rest: str) -> list[str]:
    """What a card's line says it needs, ranges read as each of their keys."""
    found = _NEEDS.search(rest)
    if not found:
        return []
    keys: list[str] = []
    for part in found["needs"].split(","):
        part = part.strip()
        if not part:
            continue
        found = _RANGE.fullmatch(part)
        if found:
            keys.extend(_expanded(found["first"], found["last"]))
            continue
        keys.append(part)
    return _unique(keys)


def _expanded(first: str, last: str) -> list[str]:
    """The keys of a range: `17.5\u201317.7` is 17.5, 17.6 and 17.7."""
    prefix, _, start = first.partition(".")
    tail, _, stop = last.partition(".")
    if prefix != tail or not start or not stop:
        return [first, last]
    if not (start.isdigit() and stop.isdigit()) or int(stop) < int(start):
        return [first, last]
    return [f"{prefix}.{one}" for one in range(int(start), int(stop) + 1)]


def _titled(text: str) -> str:
    """A line's title, without its markdown: `**Core: `OperationHints.mcp`**` is `Core: …`."""
    said = text.strip().strip("*").strip()
    said = re.sub(r"\*\*", "", said)
    return said.removesuffix(":").strip()


def _unique(keys: list[str]) -> list[str]:
    """`keys`, in order, each once."""
    seen: dict[str, None] = {}
    for key in keys:
        seen.setdefault(key, None)
    return list(seen)


def _sorted_key(key: str) -> tuple[int, int, str]:
    """A key's order: numbered keys first, in their number's order, then the plan's own."""
    return (0, int(key), "") if key.isdigit() else (1, 0, key)


class _Named(Protocol):
    """What the readers name a feature by: the parser's own `feature()`."""

    def __call__(self, key: str, title: str = "") -> _Feature: ...


class _Tasked(Protocol):
    """What they name a task by: the parser's own `task()`."""

    def __call__(self, feature_key: str, key: str, title: str) -> _Task: ...


def read_plan_file(name: str) -> ParsedPlan:
    """The plan file `name` (a `~` expanded), parsed. Raises DoesNotExist when it is not there,
    and ConfigError when it is not markdown: a file's sections become a record's spec, so the
    file is a plan's own kind (`.md`, `.markdown`), not any file on the server."""
    path = Path(name).expanduser()
    if not path.is_file():
        raise DoesNotExist(f"There is no plan file at {name!r}", hint="Give a file that is there.")
    if path.suffix.lower() not in _MARKDOWN:
        raise ConfigError(
            f"{name!r} is not a markdown file",
            hint="A plan is a .md file (its sections are a feature's spec).",
        )
    return parse_plan(path.read_text(), source=name)
