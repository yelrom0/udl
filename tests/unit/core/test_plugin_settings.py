"""
---------------------------
- test_plugin_settings.py -
---------------------------

A plugin's settings: its settings model's defaults, then its [integrations.<name>] table in the
config, then what was set through udl (get_plugin_settings / set_plugin_settings), with secrets
in the secret store and file-only settings left to the file
"""

# System Imports
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, cast

# Package Imports
import pytest
from pydantic import JsonValue, SecretStr

# Local Imports
from support.engine import databases, registry, settings
from support.settings import Configurable, ConfigurableSettings, Speed
from udl.core.models import (
    GetPluginSettingsRequest,
    PluginSettingsInfo,
    SetPluginSettingsRequest,
)
from udl.core.service import UDLService
from udl.db.records import PluginSettings, RecordQuery
from udl.errors import ConfigError, DoesNotExist, NotAllowed
from udl.plugins.registry import SITES
from udl.plugins.secrets.base import SecretRef
from udl.plugins.secrets.memory import MemorySecretStore
from udl.plugins.settings import SettingSource

type Make = Callable[..., Awaitable[UDLService]]

FILE = {"client_id": "from-the-file", "limit": 20, "roots": ["~/Media"]}


@pytest.fixture
async def make(tmp_path: Path) -> AsyncIterator[Make]:
    """Makes started services with the configurable plugin, sharing one database and one
    secret store (as the udls on a machine do)."""
    services: list[UDLService] = []
    plugins = registry()
    plugins.register(SITES, Configurable)
    db = databases(settings(tmp_path), plugins)
    secrets = MemorySecretStore(settings(tmp_path).secrets)

    async def make(integrations: dict[str, dict[str, JsonValue]] | None = None) -> UDLService:
        service = UDLService(
            settings(tmp_path, integrations=integrations or {}),
            registry=plugins,
            databases=db,
            secrets=secrets,
        )
        await service.start()
        services.append(service)
        return service

    yield make
    for service in services:
        await service.close()


async def show(service: UDLService) -> PluginSettingsInfo:
    return await service.get_plugin_settings(GetPluginSettingsRequest(plugin="configurable"))


async def change(service: UDLService, **request: Any) -> PluginSettingsInfo:
    return await service.set_plugin_settings(
        SetPluginSettingsRequest.model_validate({"plugin": "configurable", **request})
    )


# ---------------------------------------------------------------------------------------------
# Showing them
# ---------------------------------------------------------------------------------------------


async def test_a_plugin_s_settings_are_its_defaults_to_begin_with(make: Make) -> None:
    shown = await show(await make())

    assert shown.plugin == "configurable"
    assert shown.kind == "udl.sites"
    assert shown.settings == {
        "speed": "slow",
        "limit": 10,
        "client_id": None,
        "client_secret": None,
        "roots": [],
    }
    assert set(shown.sources.values()) == {SettingSource.DEFAULT}
    assert shown.secrets == ["client_secret"]
    assert shown.file_only == ["roots"]
    properties = cast(dict[str, Any], shown.json_schema["properties"])
    assert properties["roots"]["fileOnly"] is True
    assert properties["speed"]["description"] == "How fast to go."


async def test_the_config_file_s_table_is_over_the_defaults(make: Make) -> None:
    shown = await show(await make({"configurable": FILE}))

    assert (shown.settings["client_id"], shown.settings["limit"]) == ("from-the-file", 20)
    assert shown.settings["roots"] == [str(Path.home() / "Media")]
    assert shown.sources == {
        "speed": SettingSource.DEFAULT,
        "limit": SettingSource.FILE,
        "client_id": SettingSource.FILE,
        "client_secret": SettingSource.DEFAULT,
        "roots": SettingSource.FILE,
    }


async def test_secrets_are_never_shown(make: Make) -> None:
    service = await make({"configurable": {"client_secret": "file-secret"}})
    await change(service, values={"client_id": "x"})

    shown = await show(service)

    assert shown.settings["client_secret"] == "**********"  # noqa: S105 - (hidden)
    assert "file-secret" not in shown.model_dump_json()


async def test_a_plugin_without_settings_has_none_to_show(make: Make) -> None:
    service = await make()

    for name in ("http", "nope"):
        with pytest.raises(DoesNotExist, match=name) as raised:
            await service.get_plugin_settings(GetPluginSettingsRequest(plugin=name))
        assert raised.value.hint is not None
        assert "configurable" in raised.value.hint


async def test_settings_the_plugin_does_not_take_are_a_config_error(make: Make) -> None:
    service = await make({"configurable": {"limit": 1000}})

    with pytest.raises(ConfigError, match="limit") as raised:
        await show(service)

    assert raised.value.hint is not None
    assert "[integrations.configurable]" in raised.value.hint


# ---------------------------------------------------------------------------------------------
# Changing them
# ---------------------------------------------------------------------------------------------


async def test_settings_set_through_udl_are_over_the_file_s(make: Make) -> None:
    service = await make({"configurable": FILE})

    changed = await change(service, values={"speed": "fast", "limit": 30})
    again = await show(await make({"configurable": FILE}))  # (the next udl)

    assert (changed.settings["speed"], changed.settings["limit"]) == ("fast", 30)
    assert changed.settings["client_id"] == "from-the-file"
    assert (changed.sources["speed"], changed.sources["limit"]) == (
        SettingSource.STORED,
        SettingSource.STORED,
    )
    assert again == changed


async def test_setting_more_keeps_what_was_set(make: Make) -> None:
    service = await make()
    await change(service, values={"speed": "fast"})

    changed = await change(service, values={"limit": 5})

    assert (changed.settings["speed"], changed.settings["limit"]) == ("fast", 5)


async def test_unsetting_a_setting_gives_it_back_to_the_file(make: Make) -> None:
    service = await make({"configurable": FILE})
    await change(service, values={"limit": 30, "speed": "fast"})

    changed = await change(service, unset=["limit"])

    assert (changed.settings["limit"], changed.sources["limit"]) == (20, SettingSource.FILE)
    assert changed.settings["speed"] == "fast"


async def test_a_secret_goes_to_the_secret_store_not_the_database(make: Make) -> None:
    service = await make()

    changed = await change(service, values={"client_secret": "s3cret", "client_id": "id"})

    [stored] = await service.db.list_records(PluginSettings, RecordQuery())
    assert stored.values == {"client_id": "id"}
    assert stored.secrets == ["client_secret"]
    assert "s3cret" not in stored.model_dump_json()
    kept = await service.secrets.get(SecretRef("plugins/configurable/client_secret"))
    assert kept is not None
    assert kept.get_secret_value() == "s3cret"
    assert changed.sources["client_secret"] == SettingSource.STORED
    layered = await service.plugin_settings("configurable")
    assert isinstance(layered, ConfigurableSettings)
    assert layered.client_secret == SecretStr("s3cret")


async def test_unsetting_a_secret_forgets_it(make: Make) -> None:
    service = await make()
    await change(service, values={"client_secret": "s3cret"})

    changed = await change(service, unset=["client_secret"])

    assert changed.sources["client_secret"] == SettingSource.DEFAULT
    assert await service.secrets.get(SecretRef("plugins/configurable/client_secret")) is None
    assert await service.db.list_records(PluginSettings, RecordQuery()) == []


async def test_a_secret_set_to_nothing_is_forgotten(make: Make) -> None:
    service = await make()
    await change(service, values={"client_secret": "s3cret"})

    changed = await change(service, values={"client_secret": None})

    assert changed.settings["client_secret"] is None
    assert await service.secrets.get(SecretRef("plugins/configurable/client_secret")) is None


async def test_a_plugin_runs_with_its_layered_settings(make: Make) -> None:
    service = await make({"configurable": FILE})
    await change(service, values={"speed": "fast"})

    layered = await service.plugin_settings("configurable")

    assert layered == ConfigurableSettings(
        speed=Speed.FAST, limit=20, client_id="from-the-file", roots=[Path.home() / "Media"]
    )


async def test_a_file_only_setting_cannot_be_set_through_udl(make: Make) -> None:
    service = await make()

    with pytest.raises(NotAllowed, match="roots") as raised:
        await change(service, values={"roots": ["/"]})

    assert raised.value.hint is not None
    assert "config" in raised.value.hint
    assert await service.db.list_records(PluginSettings, RecordQuery()) == []


@pytest.mark.parametrize(
    ("request_", "named"),
    [
        ({"values": {"nope": 1}}, "nope"),
        ({"unset": ["nope"]}, "nope"),
        ({"values": {"limit": 1000}}, "limit"),
        ({"values": {"speed": "warp"}}, "speed"),
        ({"values": {"client_secret": 42}}, "client_secret"),
    ],
)
async def test_settings_the_plugin_does_not_take_are_refused_and_nothing_is_stored(
    make: Make, request_: dict[str, Any], named: str
) -> None:
    service = await make()

    with pytest.raises(ConfigError, match=named):
        await change(service, **request_)

    assert await service.db.list_records(PluginSettings, RecordQuery()) == []
    assert await service.secrets.get(SecretRef("plugins/configurable/client_secret")) is None


async def test_a_plugin_without_settings_cannot_be_set(make: Make) -> None:
    with pytest.raises(DoesNotExist, match="http"):
        await (await make()).set_plugin_settings(
            SetPluginSettingsRequest(plugin="http", values={"x": 1})
        )


async def test_unsetting_what_was_never_set_keeps_nothing(make: Make) -> None:
    service = await make({"configurable": FILE})

    changed = await change(service, unset=["limit", "client_secret"])

    assert changed.settings["limit"] == 20
    assert await service.db.list_records(PluginSettings, RecordQuery()) == []


async def test_a_secret_the_store_does_not_have_is_the_file_s(make: Make) -> None:
    # (e.g. settings db sync copied from another machine, whose secret store kept the secret)
    service = await make({"configurable": {"client_secret": "file-secret"}})
    await change(service, values={"client_secret": "s3cret", "limit": 5})
    await service.secrets.delete(SecretRef("plugins/configurable/client_secret"))

    layered = await service.plugin_settings("configurable")
    changed = await change(service, values={"limit": 6})

    assert isinstance(layered, ConfigurableSettings)
    assert layered.client_secret == SecretStr("file-secret")
    assert changed.settings["limit"] == 6
