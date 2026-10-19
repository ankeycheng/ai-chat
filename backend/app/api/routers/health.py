import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.api.deps import get_container
from app.container import Container

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health(c: Container = Depends(get_container)):
    return {"status": "ok", "instance": c.settings.instance_id}


@router.get("/api/ready")
async def ready(c: Container = Depends(get_container)):
    checks: dict[str, str] = {}

    async def db_check():
        async with c.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    for name, fn in (("database", db_check), ("broker", c.broker.ping)):
        try:
            await asyncio.wait_for(fn(), timeout=2)
            checks[name] = "ok"
        except Exception as e:  # noqa: BLE001
            checks[name] = f"error: {type(e).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ready" if ok else "not_ready", "checks": checks, "instance": c.settings.instance_id},
        status_code=200 if ok else 503,
    )


@router.get("/api/metrics")
async def metrics(c: Container = Depends(get_container)):
    if not c.settings.metrics_enabled:
        return Response(status_code=404)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/config")
async def public_config(c: Container = Depends(get_container)):
    """Non-sensitive capabilities for the UI."""
    s = c.settings
    return {
        "model": s.deepseek_model,
        "search_provider": s.search_provider,
        "title_generation": s.title_generation_enabled,
        "max_content_chars": s.max_content_chars,
        "broker": s.broker_backend,
        "db": "sqlite" if s.is_sqlite else "postgres",
    }
