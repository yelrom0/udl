"""
-------------
- system.py -
-------------

/databases, /databases/sync, /plugins, /plugins/{name}/settings and /config: udl itself
"""

# System Imports
from typing import Annotated

# Package Imports
from fastapi import APIRouter, Path

# Local Imports
from udl.core.models import (
    ConfigInfo,
    DatabaseList,
    GetConfigRequest,
    ListDatabasesRequest,
    ListPluginsRequest,
    PluginList,
    PluginSettingsChange,
    PluginSettingsInfo,
    PluginSettingsRequest,
    SetPluginSettingsRequest,
    SyncDatabasesRequest,
    SyncReport,
)
from udl.interfaces.api.deps import ServiceDep, documented

router = APIRouter(tags=["udl"])


@router.get("/databases", **documented("list_databases"))
async def list_databases(service: ServiceDep) -> DatabaseList:
    return await service.list_databases(ListDatabasesRequest())


@router.post("/databases/sync", **documented("sync_databases", 403, 404, 503))
async def sync_databases(request: SyncDatabasesRequest, service: ServiceDep) -> SyncReport:
    return await service.sync_databases(request)


@router.get("/plugins", **documented("list_plugins"))
async def list_plugins(service: ServiceDep) -> PluginList:
    return await service.list_plugins(ListPluginsRequest())


Plugin = Annotated[str, Path(description="The plugin: its name, as /plugins gives it.")]


@router.get("/plugins/{name}/settings", **documented("get_plugin_settings", 400, 404))
async def get_plugin_settings(name: Plugin, service: ServiceDep) -> PluginSettingsInfo:
    return await service.get_plugin_settings(PluginSettingsRequest(name=name))


@router.put("/plugins/{name}/settings", **documented("set_plugin_settings", 400, 403, 404))
async def set_plugin_settings(
    name: Plugin, change: PluginSettingsChange, service: ServiceDep
) -> PluginSettingsInfo:
    request = SetPluginSettingsRequest.model_validate({**change.model_dump(), "name": name})
    return await service.set_plugin_settings(request)


@router.get("/config", **documented("get_config"))
async def get_config(service: ServiceDep) -> ConfigInfo:
    return await service.get_config(GetConfigRequest())
