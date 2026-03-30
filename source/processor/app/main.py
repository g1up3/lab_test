"""
Processor Service - Seismic signal analysis replica.
Receives measurements from the broker, performs FFT-based frequency analysis,
classifies events, and persists them to PostgreSQL with deduplication.
Listens to the simulator's SSE control stream for shutdown commands.
"""

import asyncio
import json
import logging
import math
import os
import signal
import sys
import time
from collections import deque
from datetime import datetime, timezone

import asyncpg
import httpx
import numpy as np
import websockets
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("processor")

# Configuration
REPLICA_ID = os.getenv("REPLICA_ID", "processor-unknown")
BROKER_URL = os.getenv("BROKER_URL", "ws://broker:9000/ws/subscribe")
SIMULATOR_URL = os.getenv("SIMULATOR_URL", "http://simulator:8080")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://seismic:seismic123@postgres:5432/seismic")

WINDOW_SIZE = int(os.getenv("WINDOW_SIZE", "256"))
SAMPLING_RATE = float(os.getenv("SAMPLING_RATE_HZ", "20.0"))
ANALYZE_EVERY = int(os.getenv("ANALYZE_EVERY", "64"))
MIN_ANALYSIS_FREQ = float(os.getenv("MIN_ANALYSIS_FREQ_HZ", "0.5"))
AMPLITUDE_THRESHOLD = float(os.getenv("AMPLITUDE_THRESHOLD", "1.0"))
SNR_THRESHOLD = float(os.getenv("SNR_THRESHOLD", "5.0"))
TIME_BUCKET_SECONDS = int(os.getenv("TIME_BUCKET_SECONDS", "5"))

app = FastAPI(title=f"Seismic Processor ({REPLICA_ID})")

# State
db_pool: asyncpg.Pool | None = None
sensors_meta: dict[str, dict] = {}
windows: dict[str, deque] = {}
sample_counts: dict[str, int] = {}
start_time = time.time()
events_detected = 0
is_shutting_down = False

# SSE clients for real-time event push
sse_queues: list[asyncio.Queue] = []


# ─── FFT Analysis ─────────────────────────────────────────────

def analyze_window(sensor_id: str) -> dict | None:
    """Run FFT on the sliding window and detect events."""
    window = np.array(windows[sensor_id])
    n = len(window)

    # Apply Hanning window to reduce spectral leakage
    windowed = window * np.hanning(n)

    # Compute real FFT
    fft_result = np.fft.rfft(windowed)
    magnitudes = np.abs(fft_result) * 2.0 / n  # Normalize
    freqs = np.fft.rfftfreq(n, 1.0 / SAMPLING_RATE)

    # Ignore very low-frequency drift before selecting dominant peak.
    min_freq = MIN_ANALYSIS_FREQ
    min_idx = 0
    for i, f in enumerate(freqs):
        if f >= min_freq:
            min_idx = i
            break

    if min_idx >= len(magnitudes):
        return None

    search_mags = magnitudes[min_idx:]
    search_freqs = freqs[min_idx:]

    if len(search_mags) == 0:
        return None

    peak_idx = np.argmax(search_mags)
    peak_freq = float(search_freqs[peak_idx])
    peak_mag = float(search_mags[peak_idx])
    mean_mag = float(np.mean(search_mags))

    if mean_mag == 0:
        return None

    snr = peak_mag / mean_mag

    if peak_mag < AMPLITUDE_THRESHOLD or snr < SNR_THRESHOLD:
        return None

    event_type = classify_frequency(peak_freq)
    if event_type is None:
        return None

    return {
        "event_type": event_type,
        "dominant_frequency": round(peak_freq, 4),
        "magnitude": round(peak_mag, 4),
    }


def classify_frequency(freq: float) -> str | None:
    if 0.5 <= freq < 3.0:
        return "earthquake"
    elif 3.0 <= freq < 8.0:
        return "conventional_explosion"
    elif freq >= 8.0:
        return "nuclear_like"
    return None


def compute_time_bucket(ts: str) -> str:
    """Round timestamp to nearest TIME_BUCKET_SECONDS for deduplication."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    epoch = dt.timestamp()
    bucketed = math.floor(epoch / TIME_BUCKET_SECONDS) * TIME_BUCKET_SECONDS
    return datetime.fromtimestamp(bucketed, tz=timezone.utc).isoformat()


# ─── Database ────────────────────────────────────────────────

async def init_db():
    global db_pool
    dsn = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    while True:
        try:
            db_pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
            # Ensure table exists (idempotent)
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS detected_events (
                        id SERIAL PRIMARY KEY,
                        event_id VARCHAR(200) UNIQUE NOT NULL,
                        sensor_id VARCHAR(50) NOT NULL,
                        sensor_name VARCHAR(200),
                        region VARCHAR(200),
                        event_type VARCHAR(50) NOT NULL,
                        dominant_frequency DOUBLE PRECISION NOT NULL,
                        magnitude DOUBLE PRECISION NOT NULL,
                        detected_at TIMESTAMPTZ NOT NULL,
                        time_bucket TIMESTAMPTZ NOT NULL,
                        replica_id VARCHAR(100),
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_events_sensor ON detected_events(sensor_id);
                    CREATE INDEX IF NOT EXISTS idx_events_type ON detected_events(event_type);
                    CREATE INDEX IF NOT EXISTS idx_events_detected ON detected_events(detected_at DESC);
                """)
            logger.info("Database connected and ready")
            return
        except Exception as e:
            logger.warning(f"DB connection failed: {e}. Retrying in 3s...")
            await asyncio.sleep(3)


async def persist_event(event: dict) -> bool:
    """Insert event with ON CONFLICT DO NOTHING for deduplication. Returns True if inserted."""
    if db_pool is None:
        return False
    try:
        async with db_pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO detected_events
                    (event_id, sensor_id, sensor_name, region, event_type,
                     dominant_frequency, magnitude, detected_at, time_bucket, replica_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (event_id) DO NOTHING
                """,
                event["event_id"],
                event["sensor_id"],
                event.get("sensor_name", ""),
                event.get("region", ""),
                event["event_type"],
                event["dominant_frequency"],
                event["magnitude"],
                datetime.fromisoformat(event["detected_at"].replace("Z", "+00:00")),
                datetime.fromisoformat(event["time_bucket"].replace("Z", "+00:00")),
                REPLICA_ID,
            )
            return "INSERT" in result
    except Exception as e:
        logger.error(f"Failed to persist event: {e}")
        return False


# ─── Measurement Processing ──────────────────────────────────

async def process_measurement(msg: dict):
    """Add measurement to sliding window and trigger analysis if needed."""
    global events_detected
    sensor_id = msg["sensor_id"]

    if sensor_id not in windows:
        windows[sensor_id] = deque(maxlen=WINDOW_SIZE)
        sample_counts[sensor_id] = 0

    if sensor_id not in sensors_meta:
        sensors_meta[sensor_id] = {
            "id": sensor_id,
            "name": msg.get("sensor_name", ""),
            "category": msg.get("category", ""),
            "region": msg.get("region", ""),
        }

    windows[sensor_id].append(msg["value"])
    sample_counts[sensor_id] += 1

    if len(windows[sensor_id]) < WINDOW_SIZE:
        return

    if sample_counts[sensor_id] < ANALYZE_EVERY:
        return

    sample_counts[sensor_id] = 0

    result = analyze_window(sensor_id)
    if result is None:
        return

    time_bucket = compute_time_bucket(msg["timestamp"])
    event_id = f"{sensor_id}_{result['event_type']}_{time_bucket}"

    event = {
        "event_id": event_id,
        "sensor_id": sensor_id,
        "sensor_name": sensors_meta[sensor_id].get("name", ""),
        "region": sensors_meta[sensor_id].get("region", ""),
        "event_type": result["event_type"],
        "dominant_frequency": result["dominant_frequency"],
        "magnitude": result["magnitude"],
        "detected_at": msg["timestamp"],
        "time_bucket": time_bucket,
        "replica_id": REPLICA_ID,
    }

    inserted = await persist_event(event)
    if inserted:
        events_detected += 1
        logger.info(
            f"EVENT DETECTED: {result['event_type']} on {sensor_id} "
            f"(freq={result['dominant_frequency']}Hz, mag={result['magnitude']}) "
            f"[{REPLICA_ID}]"
        )
        # Push to SSE clients
        for q in list(sse_queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


# ─── Broker Connection ───────────────────────────────────────

async def connect_to_broker():
    """Connect to the broker's WebSocket and process incoming measurements."""
    while not is_shutting_down:
        try:
            logger.info(f"Connecting to broker at {BROKER_URL}")
            async with websockets.connect(BROKER_URL, ping_interval=20, ping_timeout=60) as ws:
                logger.info("Connected to broker")
                async for raw in ws:
                    if is_shutting_down:
                        break
                    msg = json.loads(raw)
                    if msg.get("type") == "init":
                        for s in msg.get("sensors", []):
                            sensors_meta[s["id"]] = s
                        logger.info(f"Received metadata for {len(msg.get('sensors', []))} sensors")
                        continue
                    await process_measurement(msg)
        except websockets.ConnectionClosed:
            logger.warning("Broker connection closed. Reconnecting in 2s...")
        except Exception as e:
            logger.warning(f"Broker connection error: {e}. Reconnecting in 2s...")
        await asyncio.sleep(2)


# ─── SSE Control Stream ─────────────────────────────────────

async def listen_control_stream():
    """Listen to the simulator's SSE control stream for shutdown commands."""
    while not is_shutting_down:
        try:
            logger.info(f"Connecting to control stream at {SIMULATOR_URL}/api/control")
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", f"{SIMULATOR_URL}/api/control") as response:
                    logger.info("Connected to control stream")
                    event_type = None
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_str = line[5:].strip()
                            if event_type == "command":
                                try:
                                    cmd = json.loads(data_str)
                                    if cmd.get("command") == "SHUTDOWN":
                                        logger.critical(
                                            f"SHUTDOWN command received! "
                                            f"Replica {REPLICA_ID} terminating immediately."
                                        )
                                        os._exit(1)
                                except json.JSONDecodeError:
                                    pass
                            elif event_type == "control-open":
                                logger.info(f"Control stream opened: {data_str}")
                            event_type = None
        except Exception as e:
            logger.warning(f"Control stream error: {e}. Reconnecting in 3s...")
            await asyncio.sleep(3)


# ─── FastAPI Endpoints ───────────────────────────────────────

@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(connect_to_broker())
    asyncio.create_task(listen_control_stream())
    logger.info(f"Processor {REPLICA_ID} started")


@app.on_event("shutdown")
async def shutdown():
    global is_shutting_down
    is_shutting_down = True
    if db_pool:
        await db_pool.close()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "replica_id": REPLICA_ID,
        "uptime_seconds": round(time.time() - start_time, 1),
        "sensors_tracked": len(windows),
        "events_detected": events_detected,
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
    """Query detected events with optional filters."""
    if db_pool is None:
        return JSONResponse(status_code=503, content={"error": "Database not available"})

    conditions = []
    params = []
    idx = 1

    if sensor_id:
        conditions.append(f"sensor_id = ${idx}")
        params.append(sensor_id)
        idx += 1
    if event_type:
        conditions.append(f"event_type = ${idx}")
        params.append(event_type)
        idx += 1
    if region:
        conditions.append(f"region = ${idx}")
        params.append(region)
        idx += 1
    if since:
        conditions.append(f"detected_at > ${idx}")
        params.append(datetime.fromisoformat(since.replace("Z", "+00:00")))
        idx += 1

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    async with db_pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM detected_events {where}", *params
        )
        rows = await conn.fetch(
            f"SELECT * FROM detected_events {where} ORDER BY detected_at DESC LIMIT ${idx} OFFSET ${idx+1}",
            *params, limit, offset,
        )

    events = []
    for row in rows:
        events.append({
            "id": row["id"],
            "event_id": row["event_id"],
            "sensor_id": row["sensor_id"],
            "sensor_name": row["sensor_name"],
            "region": row["region"],
            "event_type": row["event_type"],
            "dominant_frequency": row["dominant_frequency"],
            "magnitude": row["magnitude"],
            "detected_at": row["detected_at"].isoformat() if row["detected_at"] else None,
            "replica_id": row["replica_id"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        })

    return {"events": events, "total": total}


@app.get("/api/events/stream")
async def events_stream(request: Request):
    """SSE endpoint for real-time event notifications."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    sse_queues.append(queue)

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f": keepalive\n\n"
        finally:
            sse_queues.remove(queue)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/sensors")
async def get_sensors():
    """Return list of sensors this replica is tracking."""
    result = []
    for sid, meta in sensors_meta.items():
        result.append({
            "id": sid,
            "name": meta.get("name", ""),
            "category": meta.get("category", ""),
            "region": meta.get("region", ""),
            "coordinates": meta.get("coordinates"),
            "samples_in_window": len(windows.get(sid, [])),
            "window_size": WINDOW_SIZE,
        })
    return result


@app.get("/api/stats")
async def get_stats():
    """Return event statistics."""
    if db_pool is None:
        return JSONResponse(status_code=503, content={"error": "Database not available"})

    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM detected_events")
        by_type = await conn.fetch(
            "SELECT event_type, COUNT(*) as count FROM detected_events GROUP BY event_type"
        )
        by_sensor = await conn.fetch(
            "SELECT sensor_id, sensor_name, COUNT(*) as count FROM detected_events GROUP BY sensor_id, sensor_name ORDER BY count DESC"
        )
        recent = await conn.fetchval(
            "SELECT COUNT(*) FROM detected_events WHERE detected_at > NOW() - INTERVAL '5 minutes'"
        )

    return {
        "total_events": total,
        "recent_events_5min": recent,
        "by_type": {row["event_type"]: row["count"] for row in by_type},
        "by_sensor": [
            {"sensor_id": row["sensor_id"], "sensor_name": row["sensor_name"], "count": row["count"]}
            for row in by_sensor
        ],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9001)
