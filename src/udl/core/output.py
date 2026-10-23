"""
-------------
- output.py -
-------------

Where a download goes: its output dir and the filename template its downloader gets
"""

# System Imports
from pathlib import Path

# Local Imports
from udl.config import expand_path
from udl.plugins.downloaders.base import fill_fields


def resolve_output_dir(
    *,
    request: Path | None = None,
    plugin: Path | None = None,
    site: Path | None = None,
    default: Path,
) -> Path:
    """The first output dir that is set: the request's (-o/--output), a site plugin's, the
    site's default_output_dir, then the config's download_dir. `~` and `$VARS` are expanded and
    a relative dir is taken from the working dir. Nothing is created: a downloader creates the
    dir when it downloads."""
    chosen = next(path for path in (request, plugin, site, default) if path is not None)
    return expand_path(chosen).absolute()


def filename_template(template: str, site_key: str) -> str:
    """The filename template a downloader gets: `{site}` filled in with the site key (one path
    segment, which can add no fields or folders); each downloader fills in the rest."""
    return fill_fields(template, {"site": site_key})
