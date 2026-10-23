"""
-------------
- chains.py -
-------------

/chains and /sites/{key}/chain: suggest, test, save, import and export tag chains
"""

# Package Imports
from fastapi import APIRouter

# Local Imports
from udl.core.models import (
    ChainFile,
    ChainReport,
    ChainSpec,
    ChainSuggestions,
    ChainTestRequest,
    ImportChainsRequest,
    ImportedChains,
    SetChainRequest,
    SiteRequest,
    SuggestChainRequest,
)
from udl.db.models import Strategy
from udl.interfaces.api.deps import ServiceDep, documented
from udl.interfaces.api.routes.sites import Key

router = APIRouter(tags=["chains"])


@router.post("/chains/suggest", **documented("suggest_chain", 400))
async def suggest_chain(request: SuggestChainRequest, service: ServiceDep) -> ChainSuggestions:
    return await service.suggest_chain(request)


@router.post("/chains/test", **documented("test_chain", 400))
async def try_chain(request: ChainTestRequest, service: ServiceDep) -> ChainReport:
    return await service.test_chain(request)


@router.put("/sites/{key}/chain", **documented("set_site_chain", 400))
async def set_site_chain(key: Key, spec: ChainSpec, service: ServiceDep) -> Strategy:
    request = SetChainRequest.model_validate({**spec.model_dump(), "site": key})
    return await service.set_site_chain(request)


@router.post("/chains/import", **documented("import_chains", 400))
async def import_chains(request: ImportChainsRequest, service: ServiceDep) -> ImportedChains:
    return await service.import_chains(request)


@router.get("/sites/{key}/chain", **documented("export_chain", 404))
async def export_chain(key: Key, service: ServiceDep) -> ChainFile:
    return await service.export_chain(SiteRequest(site=key))
