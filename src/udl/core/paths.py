"""
------------
- paths.py -
------------

What paths an operation may be given: when the API, the MCP server or an automation calls it
(current_caller), an output dir only under api.allowed_output_roots. The CLI is the user, on
their own machine, so it may name any.
"""

# System Imports
from pathlib import Path

# Local Imports
from udl.config import Settings, expand_path
from udl.core.operations import checks_paths
from udl.errors import NotAllowed


def check_output_dir(settings: Settings, output_dir: Path | None) -> None:
    """Raise NotAllowed unless `output_dir` (a request's) is in one of api.allowed_output_roots,
    when the operation's caller is one whose paths are checked (checks_paths()).

    `~` and `$VARS` are expanded, a relative dir is taken from the working dir, and `..` and
    links are followed first, so neither can lead out of a root.
    """
    if output_dir is None or not checks_paths():
        return
    target = expand_path(output_dir).resolve()
    allowed = [root.resolve() for root in settings.api.allowed_output_roots]
    if not any(target.is_relative_to(root) for root in allowed):
        roots = ", ".join(map(str, allowed)) or "none"
        raise NotAllowed(
            f"Downloading to {target} is not allowed (it is not in api.allowed_output_roots)",
            hint=f"Choose a folder in one of the allowed output roots ({roots}), or leave the "
            "output dir out to use the default one.",
        )
