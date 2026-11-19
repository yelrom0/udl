"""
-------------
- models.py -
-------------

What a project and a repo are: a Project (a body of work and its merge policy) and a Repo (a
GitHub repository of a project, with its base branch, its gate, its checkouts and how its task
branches are named).

These are not records: they are what the orch operations take and answer, and what the project
card's fields say. orch.records holds them as records, typed with the same aliases, so a stored
project or repo is the same shape.

A branch pattern is checked as it is read, so a repo that is stored renders its branches the same
way tomorrow: it may only name the parts udl fills in ({type}, {key}, {slug}), each once, and
what is left must read as one git ref.
"""

# System Imports
import string
from enum import StrEnum
from typing import Annotated, Final

# Package Imports
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

# Local Imports
from udl.db.records import Name

__all__ = [
    "BRANCH_PARTS",
    "DEFAULT_BRANCH_PATTERN",
    "DEFAULT_GATE",
    "REF_MARKS",
    "BranchName",
    "BranchPattern",
    "Gate",
    "GateArg",
    "GitHubAccount",
    "GitHubSlug",
    "HostName",
    "MergePolicy",
    "Project",
    "Promotion",
    "Repo",
    "Title",
    "branch_pattern_problem",
    "render_branch",
    "slugify",
]

BRANCH_PARTS: tuple[str, ...] = ("type", "key", "slug")
"""What a branch pattern's {blocks} may be: the kind of change, the task's key, its slug."""

REF_MARKS: Final[str] = "~^:?*[\\"
"""What a git ref may not hold (git check-ref-format)."""

BranchName = Annotated[str, StringConstraints(pattern=r"^[^\s~^:?*\[\\]+$", max_length=200)]
"""A git branch's name (no whitespace, no ref punctuation)."""

GitHubSlug = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")]
"""A repository on GitHub: owner/repo."""

HostName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=64)]
"""The name of a host (as udl.hosts knows it)."""

GitHubAccount = Annotated[str, StringConstraints(pattern=r"^github:[A-Za-z0-9][A-Za-z0-9_.-]*$")]
"""A github account, as udl names one: github:<name> (25.4)."""

BranchPattern = Annotated[str, StringConstraints(max_length=200)]
"""A pattern like `{type}/{key}-{slug}`: udl's parts, and / - . _ between them."""

DEFAULT_BRANCH_PATTERN: str = "{type}/{key}-{slug}"
"""The pattern a repo gets unless it says otherwise (23.1's feat/<task>-<slug>)."""

DEFAULT_GATE: list[str] = ["make", "check"]
"""The gate argv a repo gets unless it says otherwise (23.1)."""


class MergePolicy(StrEnum):
    """Who merges a ready PR (O-D3): a human, or the task's own agent when it is green."""

    HUMAN = "human"
    AGENT = "agent"


def _an_argv(argv: list[str]) -> list[str]:
    """A gate's argv, as udl runs it: a program and its arguments. Raises ValueError otherwise.

    It is an argv, not a shell line: a shell line is one argument with a space in it, so an
    argument that has one is refused rather than run as one word."""
    if not argv:
        raise ValueError("is an argv: a program and its arguments (it is empty)")
    for part in argv:
        if not part:
            raise ValueError("has an empty argument (an argv's arguments are not empty)")
        if any(char.isspace() for char in part):
            raise ValueError(f"has an argument with a space in it ({part!r}): a gate is an argv")
    return argv


GateArg = Annotated[str, StringConstraints(min_length=1)]
"""One argument of a gate argv (an empty argument is a mistake, never meant)."""

Gate = Annotated[list[GateArg], AfterValidator(_an_argv)]
"""A gate's argv: a program, then its arguments — at least one, none of them empty.

It is an argv, not a shell line: `["make", "check"]`, never `["make check"]`, which is one
argument with a space in it."""


def slugify(text: str) -> str:
    """`text` as a branch's tail: lower case, and anything but a letter or digit a dash."""
    slug = "".join(char.lower() if char.isalnum() else "-" for char in text.strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def branch_pattern_problem(pattern: str) -> str | None:
    """What is wrong with `pattern` as a repo's branch pattern, or None when it is one.

    Only udl's parts may be named ({type}, {key}, {slug}), each once; the pattern may not
    change how they are named (text before the first part, or after the last, would render
    the branches of two repos apart); and what a branch is made of must read as one git ref: no
    whitespace, no empty part, no trailing dot and nothing git reads specially."""
    # (The pattern's parts, once each, and its fixed text: the text is what a branch is, once
    # its parts are filled in, so it is checked as the text. The parts are read before anything
    # is rendered, so a pattern naming a part udl does not fill in is refused, not a KeyError.)
    try:
        parsed = list(string.Formatter().parse(pattern))
    except ValueError:
        return "is not a pattern (its braces do not match)"
    blocks = [field for _, field, _, _ in parsed if field is not None]
    unknown = [block for block in blocks if block not in BRANCH_PARTS]
    if unknown:
        return f"may only name {', '.join(BRANCH_PARTS)} (not {', '.join(unknown)})"
    fixed = render_branch(pattern, type="x", key="1", slug="x")
    if not blocks:
        return "names none of the parts udl fills in"
    if len(blocks) != len(set(blocks)):
        return "names one of its parts twice"
    first = pattern.split("{", 1)[0]
    if first:
        return "has text before its first part (a pattern begins with one, e.g. {type})"
    if pattern.rfind("}") < pattern.rfind("/"):
        return "has text after its last part (a pattern ends with a part, e.g. {slug})"
    if any(char.isspace() for char in fixed) or "\\" in fixed:
        return "is one branch name, with no spaces"
    if fixed.startswith(("/", ".")) or fixed.endswith(("/", ".")):
        return "does not start or end with / or ."
    if "//" in fixed or "@{" in fixed:
        return "has no empty part or @{"
    # (What git refuses, on the branch as it renders: a component that begins with a dot or at,
    # one that ends in `.lock`, and `..` anywhere. `.locker` and `x.lock-1` are both fine.)
    for part in fixed.split("/"):
        if part.startswith((".", "@")):
            return "has no part starting with . or @"
        if part.endswith(".lock"):
            return "has no part ending in .lock"
    if ".." in fixed:
        return "has no part with .. in it"
    if not render_branch(pattern, type="feat", key="1.1", slug="x"):
        return "renders an empty branch name"
    return None


def render_branch(pattern: str, *, type: str, key: str, slug: str) -> str:  # noqa: A002 - (as a pattern names it)
    """The pattern with udl's parts filled in: the task's kind of change, its key and its slug."""
    return pattern.format(type=type, key=key, slug=slugify(slug))


def _checked_branch_pattern(pattern: str) -> str:
    """A repo's branch pattern, as udl can render it. Raises ValueError otherwise."""
    problem = branch_pattern_problem(pattern)
    if problem is not None:
        raise ValueError(f"a repo's branch pattern {problem}")
    return pattern


CheckedBranchPattern = Annotated[BranchPattern, AfterValidator(_checked_branch_pattern)]
"""A branch pattern, checked: only udl's parts, once each, and one git ref."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


def _promotion(branches: list[str]) -> list[str]:
    """The branches after a repo's base, in order: no branch twice."""
    if len(branches) != len(set(branches)):
        raise ValueError("names one of its branches twice")
    return branches


Promotion = Annotated[list[BranchName], AfterValidator(_promotion)]
"""The branches after the repo's base, in order (e.g. ["rc", "main"]); each once."""

Title = Annotated[str, StringConstraints(min_length=1)]
"""How a project is shown (e.g. udl): not empty."""


class Project(_Model):
    """A body of work (udl, liively): its repos and its merge policy.

    `promotion` names the branches after the repo's base, in order (e.g. ["rc", "main"]); a
    project whose base is its last branch (udl's `main`) has none, and its reviewer's merge is
    its review. `rules` is the markdown every agent on this project is given: the ground rules."""

    name: Name
    """What the project is called (unique; e.g. udl)."""
    title: Title
    """How it is shown (e.g. udl)."""
    description: str = ""
    """What it is, in a line or two."""
    merge: MergePolicy = MergePolicy.HUMAN
    """Who merges a ready PR (O-D3)."""
    promotion: Promotion = Field(default_factory=list)
    """The branches after the repo's base, in order (e.g. ["rc", "main"]); empty for udl."""
    review_bot: str | None = None
    """The review bot's login, whose verdict a ready PR waits on (e.g. claude[bot])."""
    rules: str = ""
    """The ground rules, as markdown, given to every agent on this project."""


class Repo(_Model):
    """A GitHub repository of a project: where it is, what it is called, its base branch, its
    gate, its main checkout on each host, and how its task branches are named."""

    project: Name
    """The project it belongs to."""
    name: Name
    """What the repo is called within the project (unique with its project)."""
    github: GitHubSlug
    """Its GitHub repository: owner/repo."""
    account: GitHubAccount
    """The github account udl reaches it through (25.4)."""
    base_branch: BranchName = "main"
    """The branch PRs go into."""
    gate: Gate = Field(default_factory=lambda: list(DEFAULT_GATE))
    """The argv that is its gate (e.g. ["make", "check"]), run before a push."""
    checkouts: dict[HostName, str] = Field(default_factory=dict)
    """Host name → the main checkout's path there (empty: udl does not know where it is yet)."""
    branch_pattern: CheckedBranchPattern = DEFAULT_BRANCH_PATTERN
    """How its task branches are named: {type}/{key}-{slug} (23.1)."""

    def branch(self, *, type: str, key: str, slug: str) -> str:  # noqa: A002 - (as its pattern names it)
        """The branch for a task of this repo: its pattern with `type`, `key` and `slug` in it.

        The slug is slugified (lower case, dashes), so a task's title is a branch's tail."""
        return render_branch(self.branch_pattern, type=type, key=key, slug=slug)
