from fastapi import APIRouter, Query

from app.api.deps import AnomalyBoardServiceDep, SettingsDep
from app.models.anomaly import AnomalyBoard

router = APIRouter()


@router.get("/anomalies", response_model=AnomalyBoard)
async def get_anomalies(
    service: AnomalyBoardServiceDep,
    settings: SettingsDep,
    limit: int | None = Query(default=None, ge=1, le=50),
) -> AnomalyBoard:
    """The most anomalous cities on Earth right now, ranked by standardized anomaly.

    Reads the stored board written by the scheduled sweep. Before the first sweep
    completes this returns 200 with an empty board and ``source: "unavailable"``
    rather than an error -- a dashboard should not break because a background job
    has not run yet.
    """
    return await service.get_board(limit or settings.anomaly_default_limit)


@router.post("/anomalies/refresh", response_model=AnomalyBoard)
async def refresh_anomalies(service: AnomalyBoardServiceDep) -> AnomalyBoard:
    """Force a sweep now.

    Exists because the scheduled interval is hours long, which would otherwise
    make the feature impossible to exercise by hand after a deploy.
    """
    return await service.sweep()
