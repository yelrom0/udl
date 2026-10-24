"""
------------------
- test_config.py -
------------------

Settings: platformdirs defaults, the TOML config file, UDL_* environment variables
"""

# System Imports
import re
from datetime import timedelta
from pathlib import Path

# Package Imports
import platformdirs
import pytest
from pydantic import ValidationError

# Local Imports
from udl.config import DBConnectionConfig, DBSettings, ReadPolicy, Settings, WritePolicy
from udl.errors import ConfigError

# The configuration example from planning/01-core.md.
EXAMPLE_CONFIG = """
download_dir = "~/Downloads/udl"
filename_template = "{site}/{title}.{ext}"
max_concurrent = 4
max_per_site = 2
probe_order = ["http", "yt-dlp", "gallery-dl", "scraper", "browser"]
heal_threshold = 3
cookies_from_browser = "firefox"          # optional
plugin_dirs = ["~/.config/udl/plugins"]

[db]
read_policy = "first"                     # first | merge
write_policy = "primary"                  # primary | all | ["local", "shared"]

[[db.connections]]
name = "local"
plugin = "sqlite"
url = "sqlite+aiosqlite:///~/.local/share/udl/udl.db"
primary = true
priority = 0                              # lower = consulted first

[[db.connections]]
name = "shared"
plugin = "postgres"
url = "postgresql+psycopg://udl@nas/udl"
priority = 10
read_only = true

[api]
host = "127.0.0.1"
port = 8765
api_key = ""                              # set to require X-API-Key
allowed_output_roots = ["~/Downloads"]

[browser]
headless = true
profile_dir = "~/.local/share/udl/browser-profile"

[tools.yt-dlp]                            # validated by YtDlpOptions
format = "bv*+ba/b"
"""


def write_config(text: str, path: Path | None = None) -> Path:
    """Write a config file (by default the one in the user config dir) and return its path."""
    path = path or platformdirs.user_config_path("udl") / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_defaults_come_from_the_user_dirs(isolated_user_dirs: Path) -> None:
    settings = Settings()

    downloads = platformdirs.user_downloads_path()
    data = platformdirs.user_data_path("udl")
    assert settings.download_dir == downloads / "udl"
    assert settings.download_dir.is_relative_to(isolated_user_dirs)
    assert settings.api.allowed_output_roots == [downloads]
    assert settings.plugin_dirs == [platformdirs.user_config_path("udl") / "plugins"]
    assert settings.browser.profile_dir == data / "browser-profile"
    assert settings.db.connections == [
        DBConnectionConfig(
            name="local",
            plugin="sqlite",
            url=f"sqlite+aiosqlite:///{data / 'udl.db'}",
            primary=True,
        )
    ]


def test_the_probe_chain_checks_whether_a_url_is_a_file_before_yt_dlp_guesses() -> None:
    # http's HEAD request is cheap and certain; yt-dlp's generic extractor is neither.
    assert Settings().probe_order == ["http", "yt-dlp", "gallery-dl", "scraper", "browser"]


@pytest.mark.parametrize("template", ["../{title}.{ext}", "/abs/{title}", "{title!r}", ""])
def test_a_filename_template_that_could_leave_the_download_dir_is_a_config_error(
    template: str,
) -> None:
    write_config(f"filename_template = {template!r}\n")

    with pytest.raises(ConfigError, match="filename_template"):
        Settings.load()


def test_load_reads_the_config_file_in_the_user_config_dir() -> None:
    write_config("max_concurrent = 8\n")

    assert Settings.load().max_concurrent == 8


def test_load_without_a_config_file_gives_the_defaults() -> None:
    assert Settings.load() == Settings()


def test_load_reads_an_explicit_config_file(tmp_path: Path) -> None:
    path = write_config("max_concurrent = 8\n", tmp_path / "custom.toml")

    assert Settings.load(path).max_concurrent == 8


def test_load_reads_the_config_file_named_by_udl_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config("max_concurrent = 8\n", tmp_path / "custom.toml")
    monkeypatch.setenv("UDL_CONFIG", str(path))

    assert Settings.load().max_concurrent == 8


def test_a_config_file_that_was_asked_for_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"missing\.toml"):
        Settings.load(tmp_path / "missing.toml")


def test_env_vars_override_the_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    write_config("max_concurrent = 8\n")
    monkeypatch.setenv("UDL_MAX_CONCURRENT", "16")

    assert Settings.load().max_concurrent == 16


def test_nested_env_vars_merge_into_config_file_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    write_config('[api]\nhost = "192.168.1.10"\n')
    monkeypatch.setenv("UDL_API__PORT", "9000")

    api = Settings.load().api

    assert (api.host, api.port) == ("192.168.1.10", 9000)


def test_paths_expand_the_home_dir_and_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDIA", "/srv/media")
    write_config('download_dir = "~/dl"\nplugin_dirs = ["$MEDIA/plugins"]\n')

    settings = Settings.load()

    assert settings.download_dir == Path.home() / "dl"
    assert settings.plugin_dirs == [Path("/srv/media/plugins")]


@pytest.mark.parametrize(
    ("toml", "problem"),
    [
        ("max_concurent = 3\n", "max_concurent: Extra inputs are not permitted"),
        ("[api]\nprot = 9000\n", "api.prot: Extra inputs are not permitted"),
        ("max_concurrent = 0\n", "max_concurrent: Input should be greater than 0"),
        ("[api]\nport = 70000\n", "api.port: Input should be less than or equal to 65535"),
        ("max_concurrent = \n", "Could not parse"),
    ],
)
def test_an_invalid_config_raises_config_error_naming_the_problem(toml: str, problem: str) -> None:
    path = write_config(toml)

    with pytest.raises(ConfigError, match=re.escape(problem)) as excinfo:
        Settings.load()

    assert str(path) in f"{excinfo.value} {excinfo.value.hint}"


def test_an_empty_api_key_means_no_key() -> None:
    write_config('[api]\napi_key = ""\n')

    assert Settings.load().api.api_key is None


def test_the_api_key_is_kept_secret() -> None:
    write_config('[api]\napi_key = "s3cret"\n')

    settings = Settings.load()

    assert settings.api.api_key is not None
    assert settings.api.api_key.get_secret_value() == "s3cret"
    assert "s3cret" not in repr(settings)
    assert "s3cret" not in settings.model_dump_json()


def test_the_documented_example_config_loads() -> None:
    write_config(EXAMPLE_CONFIG)

    settings = Settings.load()

    assert settings.db.read_policy is ReadPolicy.FIRST
    assert settings.db.write_policy is WritePolicy.PRIMARY
    assert [c.name for c in settings.db.connections] == ["local", "shared"]
    assert settings.db.connections[1].read_only
    assert settings.cookies_from_browser == "firefox"
    assert settings.tools == {"yt-dlp": {"format": "bv*+ba/b"}}
    assert settings.browser.profile_dir == Path.home() / ".local/share/udl/browser-profile"


LOCAL: dict[str, object] = {
    "name": "local",
    "plugin": "sqlite",
    "url": "sqlite+aiosqlite:///udl.db",
    "primary": True,
}
SHARED: dict[str, object] = {
    "name": "shared",
    "plugin": "postgres",
    "url": "postgresql+psycopg://udl@nas/udl",
    "read_only": True,
}


@pytest.mark.parametrize(
    ("db", "problem"),
    [
        ({"connections": []}, "at least 1 item"),
        ({"connections": [LOCAL, LOCAL]}, "duplicate connection name 'local'"),
        ({"connections": [LOCAL, {**LOCAL, "name": "other"}]}, "more than one primary connection"),
        (
            {"connections": [{**SHARED, "primary": True}]},
            "primary connection 'shared' is read-only",
        ),
        (
            {"connections": [LOCAL], "write_policy": ["nope"]},
            "write_policy names unknown connection 'nope'",
        ),
        (
            {"connections": [LOCAL, SHARED], "write_policy": ["shared"]},
            "write_policy names read-only connection 'shared'",
        ),
    ],
)
def test_contradictory_db_settings_are_rejected(db: dict[str, object], problem: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(problem)):
        DBSettings.model_validate(db)


def test_write_policy_can_name_connections() -> None:
    writable_shared = {**SHARED, "read_only": False}

    db = DBSettings.model_validate(
        {"connections": [LOCAL, writable_shared], "write_policy": ["local", "shared"]}
    )

    assert db.write_policy == ["local", "shared"]


# ---------------------------------------------------------------------------------------------
# Automations, secrets, integrations, AI and the web UI (planning.md's configuration example)
# ---------------------------------------------------------------------------------------------

AUTOMATIONS_CONFIG = """
[automations]
enabled = true                            # run the runtime in `udl serve`
max_concurrent_runs = 4
max_event_depth = 8                       # events caused by runs, caused by events, …
event_retention_days = 30
scripts_dir = "~/.config/udl/scripts"     # script actions run programs from here only
timezone = ""                             # schedules' default; empty = the system's

[secrets]
store = "auto"                            # auto (keyring, else file) | keyring | file | memory

[integrations.gmail]
client_id = "1234-abc.apps.googleusercontent.com"   # your Desktop-app OAuth client
client_secret = "GOCSPX-…"                # moved to the secret store when set through udl
poll_interval = "60s"
attachment_roots = ["~/Downloads"]        # file-only: what the API and MCP may attach

[integrations.slack]                      # nothing needed: each account brings its app's tokens

[ai]
default_provider = "local"
approval_timeout = "24h"

[web]
enabled = true
session_hours = 12
ui_plugin_dirs = ["~/.config/udl/ui-plugins"]
"""


def test_the_documented_automations_config_loads() -> None:
    write_config(AUTOMATIONS_CONFIG)

    settings = Settings.load()

    automations = settings.automations
    assert (automations.enabled, automations.max_concurrent_runs) == (True, 4)
    assert (automations.max_event_depth, automations.event_retention_days) == (8, 30)
    assert automations.scripts_dir == Path.home() / ".config/udl/scripts"
    assert automations.timezone == ""
    assert settings.secrets.store == "auto"
    assert settings.integrations["gmail"]["poll_interval"] == "60s"
    assert settings.integrations["slack"] == {}
    assert (settings.ai.default_provider, settings.ai.approval_timeout) == (
        "local",
        timedelta(hours=24),
    )
    assert (settings.web.enabled, settings.web.session_hours) == (True, 12)
    assert settings.web.ui_plugin_dirs == [Path.home() / ".config/udl/ui-plugins"]


def test_the_new_tables_have_defaults(isolated_user_dirs: Path) -> None:
    settings = Settings()

    config = isolated_user_dirs / ".config" / "udl"
    assert settings.automations.scripts_dir == config / "scripts"
    assert settings.automations.enabled is True
    assert settings.web.ui_plugin_dirs == [config / "ui-plugins"]
    assert settings.ai.default_provider is None
    assert settings.ai.approval_timeout == timedelta(hours=24)
    assert settings.integrations == {}


@pytest.mark.parametrize(
    ("written", "duration"),
    [
        ("30s", timedelta(seconds=30)),
        ("5m", timedelta(minutes=5)),
        ("24h", timedelta(hours=24)),
        ("1h30m", timedelta(hours=1, minutes=30)),
        ("2d", timedelta(days=2)),
        ("1.5h", timedelta(minutes=90)),
        ("250ms", timedelta(milliseconds=250)),
        (" 10m ", timedelta(minutes=10)),
        (90, timedelta(seconds=90)),
        (2.5, timedelta(seconds=2.5)),
    ],
)
def test_durations_are_written_with_their_units(written: object, duration: timedelta) -> None:
    assert Settings(ai={"approval_timeout": written}).ai.approval_timeout == duration  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("written", ["soon", "5x", "-5m", "m", "", -3, True, "5 m"])
def test_what_is_not_a_duration_is_refused(written: object) -> None:
    with pytest.raises(ValidationError, match="duration"):
        Settings(ai={"approval_timeout": written})  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("duration", "written"),
    [
        (timedelta(hours=24), "24h"),
        (timedelta(hours=1, minutes=30), "1h30m"),
        (timedelta(seconds=90), "1m30s"),
        (timedelta(milliseconds=1500), "1.5s"),
        (timedelta(0), "0s"),
    ],
)
def test_durations_are_shown_as_they_are_written(duration: timedelta, written: str) -> None:
    shown = Settings(ai={"approval_timeout": duration}).model_dump(mode="json")  # pyright: ignore[reportArgumentType]

    assert shown["ai"]["approval_timeout"] == written


def test_a_duration_can_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UDL_AI__APPROVAL_TIMEOUT", "2h")

    assert Settings.load().ai.approval_timeout == timedelta(hours=2)


def test_the_timezone_is_one_the_system_knows() -> None:
    assert Settings(automations={"timezone": "Europe/London"}).automations.timezone == (  # pyright: ignore[reportArgumentType]
        "Europe/London"
    )
    with pytest.raises(ValidationError, match="Mars/Olympus_Mons"):
        Settings(automations={"timezone": "Mars/Olympus_Mons"})  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("toml", "problem"),
    [
        ("[automations]\nmax_event_depth = 0\n", "automations.max_event_depth"),
        ("[web]\nsession_hours = 0\n", "web.session_hours"),
        ("[automations]\nnope = 1\n", "automations.nope"),
        ("[integrations]\ngmail = 1\n", "integrations.gmail"),
    ],
)
def test_invalid_new_tables_are_config_errors(toml: str, problem: str) -> None:
    write_config(toml)

    with pytest.raises(ConfigError, match=re.escape(problem)):
        Settings.load()
