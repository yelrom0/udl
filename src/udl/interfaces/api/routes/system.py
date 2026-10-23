"""
-------------
- system.py -
-------------

/databases, /databases/sync, /plugins and /config: udl itself
"""

# Package Imports
from fastapi import APIRouter

# Local Imports
from udl.core.models import (
    ConfigInfo,
    DatabaseList,
    GetConfigRequest,
    ListDatabasesRequest,
    ListPluginsRequest,
    PluginList,
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


@router.get("/config", **documented("get_config"))
async def get_config(service: ServiceDep) -> ConfigInfo:
    return await service.get_config(GetConfigRequest())
