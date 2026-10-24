"""
-------------
- config.py -
-------------

Settings (pydantic-settings) and where udl keeps its files (platformdirs user directories)
"""

# System Imports
import os
import re
import tomllib
import zoneinfo
from contextvars import ContextVar
from datetime import timedelta
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
    PlainSerializer,
    PositiveInt,
    PrivateAttr,
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
from udl.plugins.downloaders.base import FilenameTemplate

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


# A duration as written in the config: a number and a unit, or several (1h30m).
_DURATION: Final = re.compile(r"(?:\d+(?:\.\d+)?(?:ms|s|m|h|d))+")
_DURATION_PART: Final = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")
_UNITS: Final = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(value: object) -> object:
    """A duration as written: `30s`, `5m`, `24h`, `1h30m`, `2d`, `250ms`, or a number of seconds
    (anything else is left for validation to refuse)."""
    if isinstance(value, bool):
        raise ValueError(f"{value!r} is not a duration, such as 30s, 5m or 1h30m")
    if isinstance(value, int | float):
        if value < 0:
            raise ValueError(f"{value!r} is not a duration: it is less than none")
        return timedelta(seconds=value)
    if isinstance(value, str):
        text = value.strip()
        if not _DURATION.fullmatch(text):
            raise ValueError(f"{value!r} is not a duration, such as 30s, 5m or 1h30m")
        seconds = sum(float(n) * _UNITS[unit] for n, unit in _DURATION_PART.findall(text))
        return timedelta(seconds=seconds)
    return value


def format_duration(duration: timedelta) -> str:
    """A duration as it is written: e.g. 24h, 1h30m, 1m30s, 1.5s."""
    seconds = duration.total_seconds()
    hours, rest = divmod(seconds, 3600)
    minutes, rest = divmod(rest, 60)
    written = "".join(
        f"{int(amount)}{unit}" for amount, unit in ((hours, "h"), (minutes, "m")) if amount
    )
    if rest or not written:
        written += f"{rest:g}s"
    return written


type Duration = Annotated[
    timedelta,
    BeforeValidator(parse_duration),
    PlainSerializer(format_duration, return_type=str, when_used="json"),
]
"""A length of time, written as `30s`, `5m`, `24h` or `1h30m` (or a number of seconds)."""


def _timezone(name: str) -> str:
    """An IANA time zone's name (or nothing, for the system's)."""
    if name:
        try:
            zoneinfo.ZoneInfo(name)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"{name!r} is not a time zone this system knows") from e
    return name


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


class AutomationsSettings(_Frozen):
    """The [automations] table: the runtime that runs subscriptions' actions (in `udl serve`)."""

    # Run the runtime in `udl serve`.
    enabled: bool = True
    max_concurrent_runs: PositiveInt = 4
    # Events caused by runs, caused by events...: deeper ones are stored, not dispatched.
    max_event_depth: PositiveInt = 8
    event_retention_days: PositiveInt = 30
    # Script actions run programs from here only.
    scripts_dir: ExpandedPath = Field(default_factory=lambda: config_dir() / "scripts")
    # Schedules' time zone (an IANA name); empty is the system's.
    timezone: Annotated[str, AfterValidator(_timezone)] = ""
    # How often the runtime dispatches the events other udls stored.
    dispatch_interval: Duration = timedelta(seconds=2)
    # How long the runtime's lease lasts unless renewed (renewed a third of the way).
    lease_ttl: Duration = timedelta(seconds=30)


class AISettings(_Frozen):
    """The [ai] table: local AI's defaults (providers, prompts and guidance are records)."""

    # The AI provider an action uses when it names none.
    default_provider: str | None = None
    # How long a run waits for a tool call to be approved, before it is denied.
    approval_timeout: Duration = timedelta(hours=24)
    # Offer Ollama (on 127.0.0.1:11434) as a provider when it answers.
    detect_ollama: bool = True
    # The programs an AI provider's `command` may start (15.3): set here only, as they run on
    # this machine.
    allowed_commands: list[str] = Field(default_factory=list[str])


class WebSettings(_Frozen):
    """The [web] table: the web UI, which `udl serve` serves at /ui."""

    enabled: bool = True
    session_hours: PositiveInt = 12
    # Folders of UI plugins (each a folder with a manifest.json).
    ui_plugin_dirs: list[ExpandedPath] = Field(
        default_factory=lambda: [config_dir() / "ui-plugins"]
    )
    # The URL other machines reach this udl at (its OAuth callbacks are under it), when it
    # serves them; None for localhost only.
    public_url: str | None = None


class SecretsSettings(_Frozen):
    """The [secrets] table: where udl keeps secrets (tokens, keys, passwords), which are never
    in a database, the config or a log."""

    # auto: the system's keyring (Secret Service, KWallet) when it has one, else the file; or a
    # secret store plugin's name: keyring, file, memory (forgotten on exit)...
    store: Annotated[str, Field(min_length=1)] = "auto"
    # The file store's file (readable by its owner only).
    file: ExpandedPath = Field(default_factory=lambda: data_dir() / "secrets.json")
    # The keyring store's service name: what the keyring lists udl's secrets under.
    keyring_service: Annotated[str, Field(min_length=1)] = APP_NAME


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
    filename_template: FilenameTemplate = "{site}/{title}.{ext}"
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
    secrets: SecretsSettings = Field(default_factory=SecretsSettings)
    automations: AutomationsSettings = Field(default_factory=AutomationsSettings)
    ai: AISettings = Field(default_factory=AISettings)
    web: WebSettings = Field(default_factory=WebSettings)
    # Per-downloader options, e.g. [tools.yt-dlp]; each downloader validates its own (task 2.1).
    tools: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    # Per-plugin settings, e.g. [integrations.gmail]: the file's layer of a plugin's settings,
    # under what is set through udl (set_plugin_settings). Each plugin validates its own.
    integrations: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    _config_file: Path | None = PrivateAttr(default=None)

    @property
    def config_file(self) -> Path | None:
        """The config file these settings were read from (load()), if there was one."""
        return self._config_file

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
            loaded = cls()
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
        loaded._config_file = path if path.is_file() else None
        return loaded
