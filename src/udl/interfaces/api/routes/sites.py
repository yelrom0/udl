"""
------------
- sites.py -
------------

/probe and /sites: what downloads a URL, and what udl knows about sites
"""

# System Imports
from typing import Annotated

# Package Imports
from fastapi import APIRouter, Path, Query

# Local Imports
from udl.core.models import (
    Deleted,
    ProbeReport,
    ProbeRequest,
    SetStrategyRequest,
    SiteInfo,
    SiteList,
    SiteRequest,
    StrategySpec,
)
from udl.core.urls import SiteKey
from udl.db.models import SiteQuery, Strategy
from udl.interfaces.api.deps import ServiceDep, documented

router = APIRouter(tags=["sites"])

Key = Annotated[SiteKey, Path(description="The site: its key (e.g. example.com) or a host.")]


@router.post("/probe", **documented("probe_url", 400))
async def probe_url(request: ProbeRequest, service: ServiceDep) -> ProbeReport:
    return await service.probe(request)


@router.get("/sites", **documented("list_sites"))
async def list_sites(query: Annotated[SiteQuery, Query()], service: ServiceDep) -> SiteList:
    return await service.list_sites(query)


@router.get("/sites/{key}", **documented("get_site", 404))
async def get_site(key: Key, service: ServiceDep) -> SiteInfo:
    return await service.get_site(SiteRequest(site=key))


@router.put("/sites/{key}/strategies", **documented("set_site_strategy", 400))
async def set_site_strategy(key: Key, spec: StrategySpec, service: ServiceDep) -> Strategy:
    request = SetStrategyRequest.model_validate({**spec.model_dump(), "site": key})
    return await service.set_site_strategy(request)


@router.delete("/sites/{key}", **documented("delete_site"))
async def delete_site(key: Key, service: ServiceDep) -> Deleted:
    return await service.delete_site(SiteRequest(site=key))
