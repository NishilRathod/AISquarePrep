from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import RedisDep

router = APIRouter()


@router.get("/health")
async def health(redis: RedisDep) -> JSONResponse:
    try:
        await redis.ping()
    except Exception:
        return JSONResponse(
            status_code=503, content={"status": "error", "redis": "unreachable"}
        )
    return JSONResponse(status_code=200, content={"status": "ok", "redis": "connected"})
