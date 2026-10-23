"""
-------------
- models.py -
-------------

The models the resolution engine and the service hand out: what was tried on a URL, jobs and
their events, and each operation's request and response
"""

# System Imports
from enum import StrEnum

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.errors import ErrorInfo


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CandidateSource(StrEnum):
    """Why a downloader was tried on a URL."""

    REQUEST = "request"  # the request named it
    PLUGIN = "plugin"  # the site's plugin chose it
    STORED = "stored"  # a stored strategy for the site
    PROBE = "probe"  # the probe chain (or the browser, after a site wanted a login)


class Attempt(_Frozen):
    """One try of a downloader on a URL."""

    tool: str
    source: CandidateSource
    signed_in: bool = False  # whether it sent the site's cookies
    error: ErrorInfo | None = None  # why it failed; None when it worked
