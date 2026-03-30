"""
Broker Service - WebSocket fan-out component.
Connects to the seismic simulator, captures measurements from all sensors,
and redistributes them to all connected processing replicas via WebSocket.
"""

import asyncio
import json
import logging
import os
import time

import httpx
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("broker")

SIMULATOR_URL = os.getenv("SIMULATOR_URL", "http://localhost:8080")

app = FastAPI(title="Seismic Broker")

# State
sensors: list[dict] = []
subscribers: set[WebSocket] = set()
start_time = time.time()
message_count = 0


async def fetch_sensors() -> list[dict]:
    """Fetch sensor list from the simulator with retry."""
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{SIMULATOR_URL}/api/devices/")
                resp.raise_for_status()
                data = resp.json()
                logger.info(f"Discovered {len(data)} sensors")
                return data
        except Exception as e:
            logger.warning(f"Failed to fetch sensors: {e}. Retrying in 3s...")
            await asyncio.sleep(3)


async def broadcast(message: dict):
    """Send a message to all connected subscribers."""
    global message_count
    message_count += 1
    data = json.dumps(message)
    dead = []
    for ws in list(subscribers):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        subscribers.discard(ws)


async def sensor_listener(sensor: dict):
    """Connect to a single sensor's WebSocket and forward measurements."""
    sensor_id = sensor["id"]
    ws_path = sensor["websocket_url"]
    uri = f"ws://{SIMULATOR_URL.replace('http://', '').replace('https://', '')}{ws_path}"
    logger.info(f"Connecting to sensor {sensor_id} at {uri}")

    while True:
        try:
            async with websockets.connect(uri) as ws:
                logger.info(f"Connected to sensor {sensor_id}")
                async for raw in ws:
                    measurement = json.loads(raw)
                    enriched = {
                        "sensor_id": sensor_id,
                        "sensor_name": sensor.get("name", ""),
                        "category": sensor.get("category", ""),
                        "region": sensor.get("region", ""),
                        "timestamp": measurement["timestamp"],
                        "value": measurement["value"],
                    }
                    await broadcast(enriched)
        except websockets.ConnectionClosed:
            logger.warning(f"Sensor {sensor_id} connection closed. Reconnecting in 2s...")
        except Exception as e:
            logger.warning(f"Sensor {sensor_id} error: {e}. Reconnecting in 2s...")
        await asyncio.sleep(2)


@app.on_event("startup")
async def startup():
    global sensors
    sensors = await fetch_sensors()
    for sensor in sensors:
        asyncio.create_task(sensor_listener(sensor))
    logger.info("Broker started, listening to all sensors")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "broker",
        "uptime_seconds": round(time.time() - start_time, 1),
        "sensors_count": len(sensors),
        "subscribers_count": len(subscribers),
        "messages_forwarded": message_count,
    }


@app.get("/sensors")
async def get_sensors():
    return sensors


@app.websocket("/ws/subscribe")
async def ws_subscribe(websocket: WebSocket):
    await websocket.accept()
    subscribers.add(websocket)
    logger.info(f"New subscriber connected. Total: {len(subscribers)}")
    try:
        # Send sensor metadata on connect
        await websocket.send_text(json.dumps({"type": "init", "sensors": sensors}))
        # Keep connection alive by reading (client can send pings)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        subscribers.discard(websocket)
        logger.info(f"Subscriber disconnected. Total: {len(subscribers)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
