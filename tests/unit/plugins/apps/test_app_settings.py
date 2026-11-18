"""
------------------------
- test_app_settings.py -
------------------------

An app's settings (the udl.apps kind): its own model's defaults, then its `[apps.<name>]` table
in the config, then what was set through udl — the same layers as any other plugin's, with its
own table in the file
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, cast

# Package Imports
import pytest
from pydantic import Field, JsonValue

# Local Imports
from support.apps import FakeApp, FakeAppSettings
from support.engine import registry, settings
from udl.core.models import (
    GetConfigRequest,
    PluginSettingsInfo,
    PluginSettingsRequest,
    SetPluginSettingsRequest,
)
from udl.core.service import UDLService
from udl.db.records import PluginSettings, RecordQuery
from udl.errors import NotAllowed
from udl.plugins.apps.base import AppSettings
from udl.plugins.registry import APPS
from udl.plugins.settings import FileOnly, SettingSource

FILE: dict[str, dict[str, JsonValue]] = {
    "fakeapp": {"every": "2s"},
    "limited": {"roots": ["/srv/media"]},
}
"""What the config file sets of the two apps' settings."""


class Limited(AppSettings):
    """An app with a setting the config file only sets (it widens what it reaches)."""

    every: str = "1s"
    """How often it ticks."""
    roots: Annotated[list[Path], FileOnly()] = Field(default_factory=list[Path])
    """Where it may write."""


class Rooted(FakeApp):
    """A fake app with a file-only setting."""

    name = "limited"
    settings_model = Limited


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    plugins = registry()
    plugins.register(APPS, FakeApp)
    plugins.register(APPS, Rooted)
    found = UDLService(
        settings(tmp_path, apps=FILE),
        registry=plugins,
    )
    await found.start()
    yield found
    await found.close()


async def test_an_app_s_settings_come_from_its_own_table(service: UDLService) -> None:
    app = cast(FakeAppSettings, await service.plugin_settings(FakeApp.name))

    assert app.every.total_seconds() == 2


async def test_an_app_s_settings_leave_another_table_alone(
    service: UDLService, tmp_path: Path
) -> None:
    found = UDLService(
        settings(tmp_path, apps={}, integrations={"fakeapp": {"every": "9s"}}),
        registry=service.registry,
        databases=service.db,
        secrets=service.secrets,
    )

    app = cast(FakeAppSettings, await found.plugin_settings(FakeApp.name))

    assert app.every.total_seconds() == 1


async def test_what_was_set_through_udl_is_over_the_file(service: UDLService) -> None:
    changed = await service.set_plugin_settings(
        SetPluginSettingsRequest(name=FakeApp.name, values={"every": "3s"}, reset=[])
    )

    assert changed.values["every"] == "3s"
    assert changed.sources["every"] is SettingSource.STORED
    app = cast(FakeAppSettings, await service.plugin_settings(FakeApp.name))
    assert app.every.total_seconds() == 3


async def test_a_file_only_setting_is_left_to_the_file(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match="roots") as raised:
        await service.set_plugin_settings(
            SetPluginSettingsRequest(name="limited", values={"roots": ["/elsewhere"]}, reset=[])
        )

    assert "apps.limited" in (raised.value.hint or "")


async def test_an_app_s_settings_say_where_each_came_from(service: UDLService) -> None:
    shown = await service.get_plugin_settings(PluginSettingsRequest(name="limited"))

    assert isinstance(shown, PluginSettingsInfo)
    assert shown.sources["roots"] is SettingSource.FILE
    assert shown.sources["every"] is SettingSource.DEFAULT
    assert shown.file_only == ["roots"]


async def test_the_config_shows_the_apps_table(service: UDLService) -> None:
    shown = await service.get_config(GetConfigRequest())

    assert shown.settings.apps == FILE


async def test_a_stored_setting_is_a_record(service: UDLService) -> None:
    await service.set_plugin_settings(
        SetPluginSettingsRequest(name=FakeApp.name, values={"loud": True}, reset=[])
    )

    found = await service.db.list_records(PluginSettings, RecordQuery(where={"plugin": "fakeapp"}))

    assert [record.values for record in found] == [{"loud": True}]
