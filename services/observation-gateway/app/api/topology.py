"""GET /topology, GET /services — spec section 10/12."""

from fastapi import APIRouter, Depends

from app.api.auth import require_api_key
from app.api.deps import get_topology_builder, get_topology_store
from app.config.settings import settings
from app.storage.interfaces import TopologyStore
from app.topology.service_topology_builder import ServiceTopologyBuilder

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/topology")
async def get_topology(
    topology_builder: ServiceTopologyBuilder = Depends(get_topology_builder),
):
    graph = await topology_builder.build(settings.default_namespace)
    return {"namespace": settings.default_namespace, "topology": graph}


@router.get("/services")
async def list_services(
    topology_store: TopologyStore = Depends(get_topology_store),
):
    graph = await topology_store.get_all()
    return {"services": sorted(graph.keys())}
