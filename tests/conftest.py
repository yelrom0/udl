"""
---------------
- conftest.py -
---------------

Fixtures shared by every test
"""

# System Imports
import gc
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

# Package Imports
import keyring
import psycopg
import pymongo
import pytest
from keyring.backends import fail
from psycopg import sql
from pymongo.errors import PyMongoError
from sqlalchemy.engine import make_url

# Local Imports
from support.antibot import AntiBotSite
from support.fixture_site import FixtureSite
from support.servers import MONGODB, POSTGRES, serving
from udl.hosts.shell import REPO_VARIABLES

TESTS_DIR = Path(__file__).parent

# A git hook exports GIT_DIR, GIT_INDEX_FILE and the rest for the repo it runs in, and `make check`
# runs under the pre-commit hook. A test that runs git with the environment it inherits would act on
# that repo: on 23 Nov one ran `git init --bare` and `git add` with the plain `subprocess`, turned
# the repo bare and wiped a worktree's index. So none of them reaches a test, however it runs git.
for _name in REPO_VARIABLES:
    os.environ.pop(_name, None)

# Tests in these directories get these markers; `network` is deselected by default.
_LAYER_MARKERS = {"integration": "integration", "smoke": "network"}

# Playwright looks for its browsers under XDG_CACHE_HOME, which the tests point elsewhere,
# so pin the real location before isolating.
_PLAYWRIGHT_BROWSERS_PATH = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(
    Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"
)


NEEDS_A_SLOT = (
    "A whole suite runs here only in one of the full-suite slots (rule 9 of "
    "planning/agent-queues/_rules.md: at most three at once). Commit with "
    "`~/.local/share/udl-agents/wait-slot git commit …` and push with "
    "`~/.local/share/udl-agents/wait-slot agent-push`, which run it for you; to check a few "
    "files first, run them without coverage: `uv run pytest <paths> -q --no-cov`."
)


def pytest_configure(config: pytest.Config) -> None:
    """Refuse a whole suite run outside a slot where the machine asks for one.

    The build agents' service sets UDL_REQUIRE_SLOT on jeff-wiggle, and `wait-slot` sets
    WAIT_SLOT_HELD for what it runs. A run with coverage is a whole suite (`make check`, and so a
    commit's hook and `agent-push`): one started by hand beside the others' made a fourth on 28 Nov,
    so it stops here, before any test. CI, and a person's own runs, never set UDL_REQUIRE_SLOT.
    """
    if not os.environ.get("UDL_REQUIRE_SLOT") or os.environ.get("WAIT_SLOT_HELD"):
        return
    if config.getoption("cov_source", default=None) and not config.getoption(
        "no_cov", default=False
    ):
        pytest.exit(NEEDS_A_SLOT, returncode=pytest.ExitCode.USAGE_ERROR)


@pytest.fixture(autouse=True)
def isolated_user_dirs(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the XDG user dirs (and so platformdirs) at a temporary home, and clear UDL_* env vars.

    Tests can never read or write the real user's config, data or downloads.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", _PLAYWRIGHT_BROWSERS_PATH)
    for var, sub in {
        "XDG_CONFIG_HOME": ".config",
        "XDG_DATA_HOME": ".local/share",
        "XDG_STATE_HOME": ".local/state",
        "XDG_CACHE_HOME": ".cache",
    }.items():
        monkeypatch.setenv(var, str(home / sub))
    config_home = home / ".config"
    config_home.mkdir(parents=True)
    (config_home / "user-dirs.dirs").write_text(f'XDG_DOWNLOAD_DIR="{home / "Downloads"}"\n')
    for name in os.environ:
        if name.startswith("UDL_"):
            monkeypatch.delenv(name)
    return home


@pytest.fixture(autouse=True)
def no_keyring() -> None:
    """Give udl a keyring that has nothing in it and keeps nothing: tests never read or write the
    user's real one (those of the keyring store set one of their own, support.secrets)."""
    keyring.set_keyring(fail.Keyring())


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Put the logging setup back: `udl` (and the servers) configure logging as they start."""
    root, udl = logging.getLogger(), logging.getLogger("udl")
    handlers, levels = list(root.handlers), (root.level, udl.level)
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(levels[0])
    udl.setLevel(levels[1])


@pytest.fixture(autouse=True, scope="module")
def _collected() -> Iterator[None]:
    """Collect the garbage as each module ends, so a leak fails in the module that made it.

    An unclosed socket or pipe left in a reference cycle warns only when it is collected, and on a
    busy worker that was often inside a later, unrelated test, where nothing said whose it was
    (Q-22, 27 Nov 2026). Collected here, it fails the module's last test at teardown instead.
    """
    yield
    gc.collect()


@pytest.hookimpl(tryfirst=True)  # before -m deselects anything
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by layer: tests/integration -> integration, tests/smoke -> network."""
    for item in items:
        layer = item.path.relative_to(TESTS_DIR).parts[0]
        if marker := _LAYER_MARKERS.get(layer):
            item.add_marker(marker)


@pytest.fixture(scope="session")
def fixture_site() -> Iterator[FixtureSite]:
    """The local fixture site (tests/support/fixture_site.py), served for the whole run."""
    with FixtureSite.serve() as site:
        yield site


@pytest.fixture(scope="session")
def _antibot_server() -> Iterator[AntiBotSite]:
    with AntiBotSite.serve() as site:
        yield site


@pytest.fixture
def antibot(_antibot_server: AntiBotSite) -> AntiBotSite:
    """The anti-bot fixture site (tests/support/antibot.py), served for the whole run, as new
    for each test."""
    _antibot_server.reset()
    return _antibot_server


# ---------------------------------------------------------------------------------------------
# Database servers (tests/support/servers.py): started the first time a test needs one
# ---------------------------------------------------------------------------------------------


def _mongo(url: str, **options: Any) -> pymongo.MongoClient[dict[str, Any]]:
    return pymongo.MongoClient(url, **options)


def _postgres_answers(url: str) -> bool:
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        return False
    return True


def _mongodb_answers(url: str) -> bool:
    with _mongo(url, serverSelectionTimeoutMS=1000) as client:
        try:
            client.admin.command("ping")
        except PyMongoError:
            return False
    return True


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[str]:
    """A PostgreSQL server's URL (UDL_TEST_POSTGRES_URL's, else a Docker container's)."""
    with serving(POSTGRES, "UDL_TEST_POSTGRES_URL", _postgres_answers) as url:
        yield url


@pytest.fixture
def postgres_url(postgres_server: str) -> Iterator[str]:
    """The URL of a new, empty PostgreSQL database, dropped after the test."""
    name = f"udl_test_{uuid4().hex}"
    with psycopg.connect(postgres_server, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        yield make_url(postgres_server).set(database=name).render_as_string(hide_password=False)
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def mongodb_server() -> Iterator[str]:
    """A MongoDB server's URL (UDL_TEST_MONGODB_URL's, else a Docker container's)."""
    with serving(MONGODB, "UDL_TEST_MONGODB_URL", _mongodb_answers) as url:
        yield url


@pytest.fixture
def mongodb_url(mongodb_server: str) -> Iterator[str]:
    """The URL of a new, empty MongoDB database, dropped after the test."""
    name = f"udl_test_{uuid4().hex}"
    yield urlunsplit(urlsplit(mongodb_server)._replace(path=f"/{name}"))
    with _mongo(mongodb_server) as client:
        client.drop_database(name)
