"""Admin API — FastAPI service backing the React admin panel.

Authentication: Bearer token (settings.admin_api_token). For production,
swap for AWS Cognito or a proper auth provider.

Routes are split by resource — see services/admin_api/routes/*.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from valgo_common.config import settings
from valgo_common.logging import get_logger, setup_logging
from valgo_common.redis_client import close_redis

from .routes import accounts, audit, data_sources, nodes, risk, signals, strategies

log = get_logger(__name__)


def auth_dep(authorization: str = Header(...)) -> None:
    """Bearer token auth — header: Authorization: Bearer <token>"""
    expected = f"Bearer {settings.admin_api_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log.info("admin_api.starting", port=settings.admin_api_port)
    yield
    await close_redis()


app = FastAPI(title="Valgo Admin API", lifespan=lifespan)

# CORS — allow the admin panel origin in dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Mount route modules — all require auth
app.include_router(strategies.router, prefix="/api/strategies", tags=["strategies"], dependencies=[Depends(auth_dep)])
app.include_router(data_sources.router, prefix="/api/data-sources", tags=["data-sources"], dependencies=[Depends(auth_dep)])
app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"], dependencies=[Depends(auth_dep)])
app.include_router(nodes.router, prefix="/api/nodes", tags=["nodes"], dependencies=[Depends(auth_dep)])
app.include_router(signals.router, prefix="/api/signals", tags=["signals"], dependencies=[Depends(auth_dep)])
app.include_router(risk.router, prefix="/api/risk", tags=["risk"], dependencies=[Depends(auth_dep)])
app.include_router(audit.router, prefix="/api/audit", tags=["audit"], dependencies=[Depends(auth_dep)])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("admin_api.main:app", host="0.0.0.0", port=settings.admin_api_port, reload=settings.is_local)
