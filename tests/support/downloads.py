"""
---------------
- downloads.py -
---------------

Helpers for tests that probe and download from the fixture site
"""

# Package Imports
from pydantic import SecretStr

# Local Imports
from support.fixture_site import COOKIE_NAME, COOKIE_VALUE, FixtureSite
from udl.plugins.downloaders.base import Cookie


def session_cookie(site: FixtureSite) -> Cookie:
    """The cookie that signs in to the fixture site's private pages."""
    host = site.url("/").split("//")[1].split(":")[0]
    return Cookie(name=COOKIE_NAME, value=SecretStr(COOKIE_VALUE), domain=host)
