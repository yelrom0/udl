"""
-------------
- config.py -
-------------

Settings (pydantic-settings) and where udl keeps its files (platformdirs user directories)
"""

# System Imports
import os
import tomllib
from contextvars import ContextVar
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Self

# Package Imports
import platformdirs
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PositiveInt,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

# Local Imports
from udl.errors import ConfigError

APP_NAME: Final[str] = "udl"


def config_dir() -> Path:
    """The user config dir, e.g. ~/.config/udl."""
    return platformdirs.user_config_path(APP_NAME, appauthor=False)


def data_dir() -> Path:
    """The user data dir, e.g. ~/.local/share/udl."""
    return platformdirs.user_data_path(APP_NAME, appauthor=False)


def downloads_dir() -> Path:
    """The user's downloads folder, e.g. ~/Downloads."""
    return platformdirs.user_downloads_path()


def default_config_file() -> Path:
    """The config file read when none is named."""
    return config_dir() / "config.toml"


def default_db_path() -> Path:
    """The default SQLite database file."""
    return data_dir() / "udl.db"


def browser_profile_dir() -> Path:
    """The browser profile udl keeps logins in (`udl browser login`)."""
    return data_dir() / "browser-profile"


def expand_path(path: Path) -> Path:
    """`path` with `~` and `$VARS` expanded."""
    return Path(os.path.expandvars(path)).expanduser()


def _empty_is_none(value: object) -> object:
    return None if value == "" else value


type ExpandedPath = Annotated[Path, AfterValidator(expand_path)]
"""A path in which `~` and `$VARS` are expanded."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReadPolicy(StrEnum):
    """How lookups use several databases."""

    FIRST = "first"  # the highest-priority connection with an answer wins
    MERGE = "merge"  # combine answers from every connection


class WritePolicy(StrEnum):
    """Which databases receive writes (a list of connection names is also allowed)."""

    PRIMARY = "primary"
    ALL = "all"


class DBConnectionConfig(_Frozen):
    """One configured database connection."""

    name: Annotated[str, Field(min_length=1)]
    plugin: str  # database plugin name, e.g. "sqlite", "postgres"
    url: str
    primary: bool = False
    priority: int = 0  # lower = consulted first
    read_only: bool = False


def _default_connections() -> list[DBConnectionConfig]:
    return [
        DBConnectionConfig(
            name="local",
            plugin="sqlite",
            url=f"sqlite+aiosqlite:///{default_db_path()}",
            primary=True,
        )
    ]


class DBSettings(_Frozen):
    """The [db] table: the connections and how they are read and written."""

    read_policy: ReadPolicy = ReadPolicy.FIRST
    write_policy: WritePolicy | list[str] = WritePolicy.PRIMARY
    connections: Annotated[list[DBConnectionConfig], Field(min_length=1)] = Field(
        default_factory=_default_connections
    )

    @model_validator(mode="after")
    def _check_connections(self) -> Self:
        by_name: dict[str, DBConnectionConfig] = {}
        for connection in self.connections:
            if connection.name in by_name:
                raise ValueError(f"duplicate connection name {connection.name!r}")
            by_name[connection.name] = connection
        primaries = [c for c in self.connections if c.primary]
        if len(primaries) > 1:
            raise ValueError("more than one primary connection")
        if primaries and primaries[0].read_only:
            raise ValueError(f"primary connection {primaries[0].name!r} is read-only")
        if isinstance(self.write_policy, list):
            for name in self.write_policy:
                if name not in by_name:
                    raise ValueError(f"write_policy names unknown connection {name!r}")
                if by_name[name].read_only:
                    raise ValueError(f"write_policy names read-only connection {name!r}")
        return self


class APISettings(_Frozen):
    """The [api] table, used by `udl serve`."""

    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8765
    # Empty means no key is required.
    api_key: Annotated[SecretStr | None, BeforeValidator(_empty_is_none)] = None
    allowed_output_roots: list[ExpandedPath] = Field(default_factory=lambda: [downloads_dir()])


class BrowserSettings(_Frozen):
    """The [browser] table, used by the browser downloader."""

    headless: bool = True
    profile_dir: ExpandedPath = Field(default_factory=browser_profile_dir)


# The config file Settings() reads; Settings.load() sets it.
_config_file: ContextVar[Path | None] = ContextVar("udl_config_file", default=None)


class Settings(BaseSettings):
    """udl's configuration.

    Precedence (highest first): values passed in, UDL_* environment variables (nested tables
    use `__`, e.g. UDL_API__PORT), the TOML config file, then the defaults below.
    Settings.load() reads the config file; Settings() alone uses only the env vars and defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="UDL_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    download_dir: ExpandedPath = Field(default_factory=lambda: downloads_dir() / APP_NAME)
    filename_template: str = "{site}/{title}.{ext}"
    max_concurrent: PositiveInt = 4
    max_per_site: PositiveInt = 2
    # The order downloaders are tried in when nothing is known about a URL: those that match it
    # offline first, then the others (see udl.core.resolver).
    probe_order: list[str] = Field(
        default_factory=lambda: ["http", "yt-dlp", "gallery-dl", "scraper", "browser"]
    )
    heal_threshold: PositiveInt = 3
    cookies_from_browser: str | None = None
    plugin_dirs: list[ExpandedPath] = Field(default_factory=lambda: [config_dir() / "plugins"])
    db: DBSettings = Field(default_factory=DBSettings)
    api: APISettings = Field(default_factory=APISettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    # Per-downloader options, e.g. [tools.yt-dlp]; each downloader validates its own (task 2.1).
    tools: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The TOML file takes the place of .env and secrets-dir support.
        toml = TomlConfigSettingsSource(settings_cls, toml_file=_config_file.get())
        return (init_settings, env_settings, toml)

    @classmethod
    def load(cls, config_file: Path | None = None) -> Self:
        """Load the settings, reading `config_file`, else $UDL_CONFIG, else the default file.

        A file that was named (argument or $UDL_CONFIG) must exist; the default one is optional.
        Raises ConfigError when the file or the env vars are invalid.
        """
        if config_file is None and (named := os.environ.get("UDL_CONFIG")):
            config_file = Path(named)
        path = expand_path(config_file) if config_file else default_config_file()
        if config_file and not path.is_file():
            raise ConfigError(f"Config file not found: {path}")

        token = _config_file.set(path)
        try:
            return cls()
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"Could not parse {path}: {e}") from e
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
            )
            raise ConfigError(
                f"Invalid settings: {problems}",
                hint=f"Check {path} and any UDL_* environment variables.",
            ) from e
        finally:
            _config_file.reset(token)
