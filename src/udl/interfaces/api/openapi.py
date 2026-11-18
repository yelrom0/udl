"""
--------------
- openapi.py -
--------------

The API's OpenAPI schema, as a file: `python -m udl.interfaces.api.openapi [--check] PATH`.

The schema is made from the settings' defaults and udl's own plugins only (no config file, no
UDL_* variable, nothing another package installed), so it is the same on every machine — and
`web/packages/api`'s client types are generated from it. `--check` says whether the file at PATH
is up to date, and fails when it is not: `make check` runs it, and a test checks the committed one.
"""

# System Imports
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

# Local Imports
from udl.config import Settings
from udl.core.service import UDLService
from udl.interfaces.api.app import create_app
from udl.plugins.registry import PluginRegistry

USAGE: Final = """\
usage: python -m udl.interfaces.api.openapi [--check] PATH

Write the API's OpenAPI schema to PATH (keys sorted), or with --check, fail unless the file at
PATH is what would be written.
"""


def schema() -> dict[str, object]:
    """The API's schema: udl's own plugins, and the settings' defaults."""
    registry = PluginRegistry.discover(builtin_only=True)
    app = create_app(
        Settings.defaults(), make_service=lambda settings: UDLService(settings, registry=registry)
    )
    return app.openapi()


def render(schema: Mapping[str, object]) -> str:
    """The schema as it is written: two-space JSON, keys sorted (so the file is stable), and a
    last newline."""
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Write the schema to the path the command line names, or check it. A path that is wrong,
    or a file that is out of date, is said and answered with 2, or 1."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args or "-h" in args:
        sys.stdout.write(USAGE)
        return 0
    check = "--check" in args
    named = [arg for arg in args if arg != "--check"]
    if len(named) != 1 or named[0].startswith("-"):
        sys.stderr.write(USAGE)
        return 2
    path = Path(named[0])
    text = render(schema())
    if check:
        current = path.read_text() if path.is_file() else None
        if current != text:
            sys.stderr.write(
                f"{path} is out of date: run `make openapi` (or "
                f"`python -m udl.interfaces.api.openapi {path}`)\n"
            )
            return 1
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
