"""
Gateway Service - Single entry point for the frontend.
Routes requests to healthy processing replicas with round-robin load balancing.
Performs periodic health checks and excludes failed replicas.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("gateway")

PROCESSOR_URLS = os.getenv("PROCESSOR_URLS", "http://processor-1:9001,http://processor-2:9001,http://processor-3:9001")
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "5"))
HEALTH_CHECK_TIMEOUT = float(os.getenv("HEALTH_CHECK_TIMEOUT", "2.0"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://seismic:seismic123@postgres:5432/seismic")
KEY_HASH_SECRET = os.getenv("KEY_HASH_SECRET", "camera-cafe-dev-secret")
BOOTSTRAP_ADMIN_KEY = os.getenv("BOOTSTRAP_ADMIN_KEY", "")

ROLES = {"viewer", "analyst", "admin"}
ROLE_RULES: dict[str, set[str]] = {
    "/api/events": {"viewer", "analyst", "admin"},
    "/api/sensors": {"viewer", "analyst", "admin"},
    "/api/stats": {"viewer", "analyst", "admin"},
    "/api/replicas": {"viewer", "analyst", "admin"},
    "/api/events/stream": {"analyst", "admin"},
    "/api/auth/me": {"viewer", "analyst", "admin"},
    "/api/admin/keys": {"admin"},
    "/api/admin/audit": {"admin"},
}
PUBLIC_PATHS = {"/health"}

app = FastAPI(title="Seismic Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# State
processors = [url.strip() for url in PROCESSOR_URLS.split(",") if url.strip()]
healthy_processors: list[str] = []
processor_status: dict[str, dict] = {}
rr_index = 0
start_time = time.time()
db_pool: asyncpg.Pool | None = None


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="viewer")
    expires_days: int | None = Field(default=None, ge=1, le=3650)


def hash_key(raw_key: str) -> str:
    digest = hmac.new(KEY_HASH_SECRET.encode(), raw_key.encode(), hashlib.sha256).hexdigest()
    return digest


def extract_api_key(request: Request) -> str | None:
    return request.headers.get("X-API-Key") or request.query_params.get("api_key")


def required_roles_for_path(path: str) -> set[str] | None:
    if path in PUBLIC_PATHS:
        return None
    if path.startswith("/api/admin/keys"):
        return {"admin"}
    return ROLE_RULES.get(path)


async def log_audit(
    key_id: int | None,
    role: str | None,
    method: str,
    path: str,
    status_code: int,
    latency_ms: int,
    client_ip: str | None,
) -> None:
    if db_pool is None:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_logs
                (api_key_id, role, method, path, status_code, latency_ms, client_ip)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                key_id,
                role,
                method,
                path,
                status_code,
                latency_ms,
                client_ip,
            )
    except Exception as e:
        logger.warning(f"Failed to write audit log: {e}")


async def resolve_key_context(request: Request) -> tuple[int, str, str]:
    if db_pool is None:
        raise RuntimeError("Database not available")

    raw_key = extract_api_key(request)
    if not raw_key:
        raise PermissionError("API key missing")

    key_hash = hash_key(raw_key)
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, role, name, is_active, expires_at
            FROM api_keys
            WHERE key_hash = $1
            """,
            key_hash,
        )

        if row is None:
            raise PermissionError("Invalid API key")
        if not row["is_active"]:
            raise PermissionError("API key revoked")
        if row["expires_at"] is not None and row["expires_at"] <= datetime.now(timezone.utc):
            raise PermissionError("API key expired")

        await conn.execute(
            "UPDATE api_keys SET last_used_at = NOW() WHERE id = $1",
            row["id"],
        )
    return row["id"], row["role"], row["name"]


@app.middleware("http")
async def auth_and_audit_middleware(request: Request, call_next):
    started = time.perf_counter()
    path = request.url.path
    method = request.method
    client_ip = request.client.host if request.client else None

    key_id = None
    role = None
    required_roles = required_roles_for_path(path)

    if required_roles is not None:
        try:
            key_id, role, key_name = await resolve_key_context(request)
            if required_roles and role not in required_roles:
                response = JSONResponse(status_code=403, content={"error": "Insufficient role"})
                await log_audit(key_id, role, method, path, 403, int((time.perf_counter() - started) * 1000), client_ip)
                return response
            request.state.auth = {
                "key_id": key_id,
                "role": role,
                "key_name": key_name,
            }
        except PermissionError as e:
            response = JSONResponse(status_code=401, content={"error": str(e)})
            await log_audit(None, None, method, path, 401, int((time.perf_counter() - started) * 1000), client_ip)
            return response
        except Exception:
            response = JSONResponse(status_code=503, content={"error": "Auth service unavailable"})
            await log_audit(None, None, method, path, 503, int((time.perf_counter() - started) * 1000), client_ip)
            return response

    response = await call_next(request)
    latency_ms = int((time.perf_counter() - started) * 1000)
    await log_audit(key_id, role, method, path, response.status_code, latency_ms, client_ip)
    return response


def get_next_healthy() -> str | None:
    """Round-robin selection of a healthy processor."""
    global rr_index
    if not healthy_processors:
        return None
    url = healthy_processors[rr_index % len(healthy_processors)]
    rr_index += 1
    return url


def mark_processor_unhealthy(url: str, status: str = "unreachable") -> None:
    """Immediately mark a processor as unavailable and remove it from RR pool."""
    global healthy_processors
    processor_status[url] = {
        "url": url,
        "status": status,
        "details": None,
        "last_check": time.time(),
    }
    healthy_processors = [p for p in healthy_processors if p != url]


async def check_health():
    """Periodically check health of all processors."""
    global healthy_processors
    while True:
        alive: list[str] = []

        async def probe(url: str, client: httpx.AsyncClient):
            try:
                resp = await client.get(f"{url}/health")
                if resp.status_code == 200:
                    alive.append(url)
                    processor_status[url] = {
                        "url": url,
                        "status": "healthy",
                        "details": resp.json(),
                        "last_check": time.time(),
                    }
                else:
                    processor_status[url] = {
                        "url": url,
                        "status": "unhealthy",
                        "details": None,
                        "last_check": time.time(),
                    }
            except Exception:
                processor_status[url] = {
                    "url": url,
                    "status": "unreachable",
                    "details": None,
                    "last_check": time.time(),
                }

        async with httpx.AsyncClient(timeout=HEALTH_CHECK_TIMEOUT) as client:
            await asyncio.gather(*(probe(url, client) for url in processors))

        if set(alive) != set(healthy_processors):
            logger.info(f"Healthy processors: {len(alive)}/{len(processors)} -> {alive}")
        healthy_processors = alive
        await asyncio.sleep(HEALTH_CHECK_INTERVAL)


async def proxy_get(path: str, params: dict = None) -> JSONResponse:
    """Proxy a GET request to a healthy processor."""
    if not healthy_processors:
        return JSONResponse(status_code=503, content={"error": "No healthy processors available"})

    attempts = len(healthy_processors)
    async with httpx.AsyncClient(timeout=10) as client:
        for _ in range(attempts):
            url = get_next_healthy()
            if not url:
                break
            try:
                resp = await client.get(f"{url}{path}", params=params)
                return JSONResponse(status_code=resp.status_code, content=resp.json())
            except Exception as e:
                logger.error(f"Proxy error from {url}: {e}")
                mark_processor_unhealthy(url)

    return JSONResponse(status_code=502, content={"error": "Upstream error"})


@app.on_event("startup")
async def startup():
    global db_pool
    dsn = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    db_pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=8)
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id SERIAL PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                role VARCHAR(20) NOT NULL,
                key_hash VARCHAR(128) UNIQUE NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_used_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_api_keys_role ON api_keys(role);
            CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(is_active);

            CREATE TABLE IF NOT EXISTS audit_logs (
                id BIGSERIAL PRIMARY KEY,
                api_key_id INTEGER REFERENCES api_keys(id) ON DELETE SET NULL,
                role VARCHAR(20),
                method VARCHAR(10) NOT NULL,
                path TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                latency_ms INTEGER NOT NULL,
                client_ip VARCHAR(120),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_status ON audit_logs(status_code);
            """
        )

        if BOOTSTRAP_ADMIN_KEY:
            admin_hash = hash_key(BOOTSTRAP_ADMIN_KEY)
            await conn.execute(
                """
                INSERT INTO api_keys(name, role, key_hash, is_active)
                VALUES ('bootstrap-admin', 'admin', $1, TRUE)
                ON CONFLICT (key_hash) DO NOTHING
                """,
                admin_hash,
            )

    asyncio.create_task(check_health())
    logger.info(f"Gateway started with {len(processors)} processors configured")


@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "gateway",
        "uptime_seconds": round(time.time() - start_time, 1),
        "healthy_replicas": len(healthy_processors),
        "total_replicas": len(processors),
    }


@app.get("/api/events")
async def get_events(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sensor_id: str | None = Query(None),
    event_type: str | None = Query(None),
    region: str | None = Query(None),
    since: str | None = Query(None),
):
    params = {"limit": limit, "offset": offset}
    if sensor_id:
        params["sensor_id"] = sensor_id
    if event_type:
        params["event_type"] = event_type
    if region:
        params["region"] = region
    if since:
        params["since"] = since
    return await proxy_get("/api/events", params)


@app.get("/api/events/stream")
async def events_stream(request: Request):
    """Proxy SSE stream from a healthy processor."""
    url = get_next_healthy()
    if not url:
        return JSONResponse(status_code=503, content={"error": "No healthy processors available"})

    async def generate():
        while True:
            target = get_next_healthy()
            if not target:
                yield f"data: {{\"error\": \"no healthy processors\"}}\n\n"
                await asyncio.sleep(2)
                continue
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", f"{target}/api/events/stream") as resp:
                        async for line in resp.aiter_lines():
                            if await request.is_disconnected():
                                return
                            yield line + "\n"
            except Exception as e:
                logger.warning(f"SSE proxy error: {e}")
                await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/sensors")
async def get_sensors():
    return await proxy_get("/api/sensors")


@app.get("/api/stats")
async def get_stats():
    return await proxy_get("/api/stats")


@app.get("/api/replicas")
async def get_replicas():
    """Return the status of all processing replicas."""
    replicas = []
    for url in processors:
        status = processor_status.get(url, {"url": url, "status": "unknown", "details": None, "last_check": None})
        replicas.append(status)
    return {
        "replicas": replicas,
        "healthy": len(healthy_processors),
        "total": len(processors),
    }


@app.get("/api/auth/me")
async def auth_me(request: Request):
    auth = request.state.auth
    return {
        "key_id": auth["key_id"],
        "key_name": auth["key_name"],
        "role": auth["role"],
    }


@app.get("/api/admin/keys")
async def admin_list_keys():
    if db_pool is None:
        return JSONResponse(status_code=503, content={"error": "Database not available"})
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, role, is_active, created_at, last_used_at, expires_at, revoked_at
            FROM api_keys
            ORDER BY id ASC
            """
        )
    keys = []
    for row in rows:
        keys.append(
            {
                "id": row["id"],
                "name": row["name"],
                "role": row["role"],
                "is_active": row["is_active"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] else None,
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
            }
        )
    return {"keys": keys}


@app.post("/api/admin/keys")
async def admin_create_key(payload: CreateApiKeyRequest):
    if payload.role not in ROLES:
        return JSONResponse(status_code=400, content={"error": "Invalid role"})
    if db_pool is None:
        return JSONResponse(status_code=503, content={"error": "Database not available"})

    raw_key = secrets.token_urlsafe(32)
    key_hash = hash_key(raw_key)
    expires_at = None
    if payload.expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_days)

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO api_keys(name, role, key_hash, expires_at)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, role, created_at, expires_at
            """,
            payload.name,
            payload.role,
            key_hash,
            expires_at,
        )

    return {
        "api_key": raw_key,
        "key": {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        },
        "note": "Save this key now. It cannot be retrieved again.",
    }


@app.delete("/api/admin/keys/{key_id}")
async def admin_revoke_key(key_id: int):
    if db_pool is None:
        return JSONResponse(status_code=503, content={"error": "Database not available"})
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE api_keys
            SET is_active = FALSE, revoked_at = NOW()
            WHERE id = $1 AND is_active = TRUE
            """,
            key_id,
        )
    if result.endswith("0"):
        return JSONResponse(status_code=404, content={"error": "Key not found or already revoked"})
    return {"status": "revoked", "key_id": key_id}


@app.get("/api/admin/audit")
async def admin_audit_logs(limit: int = Query(100, ge=1, le=1000)):
    if db_pool is None:
        return JSONResponse(status_code=503, content={"error": "Database not available"})
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, api_key_id, role, method, path, status_code, latency_ms, client_ip, created_at
            FROM audit_logs
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )

    logs = []
    for row in rows:
        logs.append(
            {
                "id": row["id"],
                "api_key_id": row["api_key_id"],
                "role": row["role"],
                "method": row["method"],
                "path": row["path"],
                "status_code": row["status_code"],
                "latency_ms": row["latency_ms"],
                "client_ip": row["client_ip"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
        )
    return {"logs": logs}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
