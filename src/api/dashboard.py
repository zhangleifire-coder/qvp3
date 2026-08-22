from fastapi import APIRouter
from src.dashboard.metrics import all_metrics

router = APIRouter()


@router.get("/api/dashboard/metrics")
async def metrics():
    return await all_metrics()
