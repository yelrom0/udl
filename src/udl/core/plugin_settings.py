"""
----------------------
- plugin_settings.py -
----------------------

A plugin's settings (udl.plugins.settings: a plugin with a settings model has them), in layers:
the model's defaults, then its [integrations.<name>] table in the config, then what was set
through udl (set_plugin_settings): a PluginSettings record, and the secret store for the
settings that are secrets. What the plugin would run with is checked before anything is kept.
"""

# System Imports
from collections.abc import Callable
from typing import Any

# Package Imports
from pydantic import BaseModel, JsonValue, ValidationError

# Local Imports
from udl.config import Settings
from udl.core.models import PluginSettingsInfo, SetPluginSettingsRequest
from udl.db.manager import DBManager
from udl.db.records import PluginSettings, RecordQuery
from udl.errors import ConfigError, DoesNotExist, NotAllowed
from udl.plugins.registry import PluginInfo, PluginRegistry
from udl.plugins.secrets.base import SecretStore
from udl.plugins.settings import (
    SettingSource,
    is_file_only,
    is_secret,
    secret_ref,
    settings_model_of,
)


class PluginSettingsManager:
    """The settings of the plugins in `registry` that have them, with the config file's layer
    in `settings`, what was set through udl in `db`, and its secrets in `secrets()` (the
    service's store, made when it is first needed)."""

    def __init__(
        self,
        settings: Settings,
        registry: PluginRegistry,
        db: DBManager,
        secrets: Callable[[], SecretStore],
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._db = db
        self._secrets = secrets

    async def settings_of(self, name: str) -> BaseModel:
        """The settings the plugin called `name` runs with (secrets readable). Raises
        DoesNotExist, or ConfigError when they are not valid."""
        _, model = self._configurable(name)
        return await self._layered(name, model, await self._stored(name))

    async def show(self, name: str) -> PluginSettingsInfo:
        """The plugin's settings, secrets hidden, and where each comes from."""
        info, model = self._configurable(name)
        return await self._info(info, model, await self._stored(info.name))

    async def change(self, request: SetPluginSettingsRequest) -> PluginSettingsInfo:
        """Set some of a plugin's settings, and give others back to the file (reset)."""
        info, model = self._configurable(request.name)
        name, fields = info.name, model.model_fields
        unknown = sorted((set(request.values) | set(request.reset)) - set(fields))
        if unknown:
            raise ConfigError(
                f"The {name} plugin has no setting {', '.join(unknown)}",
                hint=f"Its settings: {', '.join(fields)}.",
            )
        file_only = sorted(field for field in request.values if is_file_only(fields[field]))
        if file_only:
            raise NotAllowed(
                f"{', '.join(file_only)} of the {name} plugin is set in the config file only",
                hint=f"Set it in [integrations.{name}] in the config: it widens what udl "
                "reaches, so no API client or AI may change it.",
            )
        stored = await self._stored(name)
        values = {
            field: value
            for field, value in (stored.values if stored else {}).items()
            if field not in request.reset
        }
        secrets = {
            field for field in (stored.secrets if stored else []) if field not in request.reset
        }
        new_secrets: dict[str, JsonValue] = {}
        for field, value in request.values.items():
            if is_secret(fields[field]):
                new_secrets[field] = value
                values.pop(field, None)
            else:
                values[field] = value
        store = self._secrets()
        candidate: dict[str, Any] = {**self._settings.integrations.get(name, {}), **values}
        for field in secrets - set(new_secrets):
            if (kept := await store.get(secret_ref(name, field))) is not None:
                candidate[field] = kept
        candidate |= {field: value for field, value in new_secrets.items() if value is not None}
        checked = _validated(name, model, candidate)  # (raises ConfigError)

        for field in stored.secrets if stored else []:
            if field in request.reset:
                await store.delete(secret_ref(name, field))
        for field, value in new_secrets.items():
            if value is None:
                await store.delete(secret_ref(name, field))
                secrets.discard(field)
            else:
                await store.set(secret_ref(name, field), getattr(checked, field))
                secrets.add(field)
        kept_now: PluginSettings | None = None
        if values or secrets:
            kept_now = await self._db.upsert_record(
                (stored or PluginSettings(plugin=name)).model_copy(
                    update={"values": values, "secrets": sorted(secrets)}
                )
            )
        elif stored is not None:
            await self._db.delete_record(PluginSettings, stored.id)
        return await self._info(info, model, kept_now)

    def _configurable(self, name: str) -> tuple[PluginInfo, type[BaseModel]]:
        """The plugin called `name` that has settings, and its settings model. Raises
        DoesNotExist."""
        configurable = [
            (info, model)
            for info, plugin in self._registry.entries()
            if (model := settings_model_of(plugin)) is not None
        ]
        for info, model in configurable:
            if info.name == name:
                return info, model
        names = ", ".join(sorted({info.name for info, _ in configurable})) or "none"
        raise DoesNotExist(
            f"No plugin called {name!r} has settings",
            hint=f"The plugins that have settings: {names}.",
        )

    async def _stored(self, name: str) -> PluginSettings | None:
        found = await self._db.list_records(
            PluginSettings, RecordQuery(where={"plugin": name}, limit=1)
        )
        return found[0] if found else None

    async def _layered(
        self, name: str, model: type[BaseModel], stored: PluginSettings | None
    ) -> BaseModel:
        values: dict[str, Any] = {**self._settings.integrations.get(name, {})}
        if stored is not None:
            values |= stored.values
            for field in stored.secrets:
                if (secret := await self._secrets().get(secret_ref(name, field))) is not None:
                    values[field] = secret
        return _validated(name, model, values)

    async def _info(
        self, info: PluginInfo, model: type[BaseModel], stored: PluginSettings | None
    ) -> PluginSettingsInfo:
        layered = await self._layered(info.name, model, stored)
        in_file = self._settings.integrations.get(info.name, {})
        set_here = {*stored.values, *stored.secrets} if stored is not None else set[str]()
        fields = model.model_fields
        return PluginSettingsInfo(
            name=info.name,
            kind=info.kind,
            values=layered.model_dump(mode="json"),
            sources={
                field: SettingSource.STORED
                if field in set_here
                else SettingSource.FILE
                if field in in_file
                else SettingSource.DEFAULT
                for field in fields
            },
            json_schema=model.model_json_schema(),
            secrets=[field for field, found in fields.items() if is_secret(found)],
            file_only=[field for field, found in fields.items() if is_file_only(found)],
        )


def _validated(name: str, model: type[BaseModel], values: dict[str, Any]) -> BaseModel:
    """`values` as the plugin's settings. Raises ConfigError, saying what is wrong."""
    try:
        return model.model_validate(values)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(map(str, problem['loc'])) or 'settings'}: {problem['msg']}"
            for problem in e.errors()
        )
        raise ConfigError(
            f"The {name} plugin's settings are not valid: {problems}",
            hint=f"Check [integrations.{name}] in the config, and what was set through udl "
            f"(`udl plugins settings {name}`).",
        ) from e
