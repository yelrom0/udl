"""
------------------
- test_config.py -
------------------

Settings: platformdirs defaults, the TOML config file, UDL_* environment variables
"""

# System Imports
import re
from pathlib import Path

# Package Imports
import platformdirs
import pytest
from pydantic import ValidationError

# Local Imports
from udl.config import DBConnectionConfig, DBSettings, ReadPolicy, Settings, WritePolicy
from udl.errors import ConfigError

# The configuration example from planning.md.
EXAMPLE_CONFIG = """
download_dir = "~/Downloads/udl"
filename_template = "{site}/{title}.{ext}"
max_concurrent = 4
max_per_site = 2
probe_order = ["yt-dlp", "gallery-dl", "http", "scraper", "browser"]
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
