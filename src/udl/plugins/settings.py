"""
---------------
- settings.py -
---------------

Plugin settings: what a plugin (of any kind) that has a `settings_model` runs with, in layers:

1. the model's defaults;
2. its [integrations.<name>] table in the config file;
3. what was set through udl (set_plugin_settings, e.g. from its UI plugin's settings panel): a
   PluginSettings record, and the secret store for the settings that are secrets (SecretStr).

A field marked FileOnly (one that widens what an API client or an AI could reach, such as a
folder files are sent from) is set in the file only: set_plugin_settings refuses it.
"""

# System Imports
import types
import typing
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Package Imports
from pydantic import BaseModel, GetJsonSchemaHandler, SecretStr
from pydantic.fields import FieldInfo
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

# Local Imports
from udl.plugins.secrets.base import SecretRef


@dataclass(frozen=True)
class FileOnly:
    """Marks a settings field that only the config file sets: `Annotated[list[Path],
    FileOnly()]`. Its JSON Schema says so (`fileOnly`), for a settings panel to show."""

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        found = handler(schema)
        found["fileOnly"] = True
        return found


class SettingSource(StrEnum):
    """Where a setting's value comes from."""

    DEFAULT = "default"  # the plugin's settings model
    FILE = "file"  # its [integrations.<name>] table
    STORED = "stored"  # set through udl (set_plugin_settings)


def settings_model_of(plugin: type) -> type[BaseModel] | None:
    """A plugin class's settings model, if it has one."""
    model: object = getattr(plugin, "settings_model", None)
    if isinstance(model, type) and issubclass(model, BaseModel):
        return model
    return None


def is_secret(field: FieldInfo) -> bool:
    """Whether a settings field is a secret (a SecretStr, or None)."""
    annotation: Any = field.annotation
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        return SecretStr in typing.get_args(annotation)
    return annotation is SecretStr


def is_file_only(field: FieldInfo) -> bool:
    return any(isinstance(found, FileOnly) for found in field.metadata)


def secret_ref(plugin: str, field: str) -> SecretRef:
    """Where a plugin's secret setting is kept in the secret store."""
    return SecretRef.of("plugins", plugin, field)
