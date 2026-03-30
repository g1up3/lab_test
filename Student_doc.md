# Student Documentation - CAMERA CAFE

## Project Name
**CAMERA CAFE** - Seismic Intelligence Monitoring Platform

## Team
| Role | Name |
|------|------|
| Developer | *(fill in)* |

---

## 1. System Architecture

### 1.1 High-Level Overview

The system follows a distributed microservice architecture with the following components:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        NEUTRAL REGION                               │
│  ┌─────────┐     ┌────────┐     ┌─────────┐     ┌──────────────┐  │
│  │Simulator│────►│ Broker  │────►│ Gateway │────►│   Frontend   │  │
│  │ (8080)  │     │ (9000)  │     │ (8081)  │     │   (3000)     │  │
│  └────┬────┘     └───┬────┘     └────┬────┘     └──────────────┘  │
│       │              │               │                              │
│       │         ┌────┴─────────┬─────┴──────┐                      │
│       │         │              │             │                      │
│  ┌────┴──────┐  │              │             │                      │
│  │  SSE      │  │              │             │                      │
│  │ Control   │  │              │             │                      │
│  │ Stream    │  │              │             │                      │
│  └───┬──┬──┬─┘  │              │             │                      │
└──────┼──┼──┼────┼──────────────┼─────────────┼──────────────────────┘
       │  │  │    │              │             │
┌──────┼──┼──┼────┼──────────────┼─────────────┼──────────────────────┐
│      │  │  │ DATACENTER REGION │             │                      │
│  ┌───▼──┼──┼───┐ ┌────────────▼┐ ┌──────────▼─┐                   │
│  │Processor-1  │ │ Processor-2 │ │ Processor-3 │                   │
│  │  (9001)     │ │   (9001)    │ │   (9001)    │                   │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘                   │
│         │               │               │                           │
│         └───────────────┼───────────────┘                           │
│                         │                                           │
│                  ┌──────▼──────┐                                    │
│                  │  PostgreSQL │                                    │
│                  │   (5432)    │                                    │
│                  └─────────────┘                                    │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Component Descriptions

| Component | Technology | Port | Description |
|-----------|-----------|------|-------------|
| **Simulator** | Provided Docker image | 8080 | Generates seismic data for 12 sensors via WebSocket |
| **Broker** | Python/FastAPI | 9000 | Connects to all sensor WebSockets, fan-out broadcasts to replicas |
| **Processor** (x3) | Python/FastAPI + NumPy | 9001 | FFT analysis, event classification, DB persistence |
| **Gateway** | Python/FastAPI | 8081 | Health-checked reverse proxy with round-robin load balancing |
| **Frontend** | React + Vite + Nginx | 3000 | Real-time monitoring dashboard |
| **PostgreSQL** | PostgreSQL 16 | 5432 | Shared persistent event storage |

### 1.3 Data Flow

1. **Ingestion**: Simulator → Broker (WebSocket per sensor, 20 Hz each)
2. **Distribution**: Broker → All Processors (WebSocket broadcast, fan-out)
3. **Analysis**: Processor receives sample → adds to sliding window → every 64 samples, runs FFT → classifies event
4. **Persistence**: Processor → PostgreSQL (INSERT ON CONFLICT DO NOTHING for deduplication)
5. **Presentation**: Frontend → Gateway → Healthy Processor → PostgreSQL → Response

### 1.4 Failure Model

- **Only processors are subject to failure** (via simulator SHUTDOWN commands).
- The broker, gateway, database, and frontend are assumed reliable.
- Docker `restart: always` ensures processors automatically recover.
- The gateway detects failures within 5 seconds and reroutes traffic.

---

## 2. Technology Stack

| Layer | Technology | Justification |
|-------|-----------|---------------|
| Backend | Python 3.12 + FastAPI | Excellent async support for WebSocket/SSE handling; NumPy ecosystem for FFT |
| FFT Analysis | NumPy | Industry-standard, optimized FFT implementation |
| Database | PostgreSQL 16 | Robust relational DB with native conflict handling for deduplication |
| DB Driver | asyncpg | High-performance async PostgreSQL driver |
| Gateway | Python/FastAPI | Lightweight, consistent with backend stack |
| Frontend | React 18 + Vite | Fast build tooling, modern component model |
| Serving | Nginx | Efficient static file serving + reverse proxy to gateway |
| Orchestration | Docker Compose | Single-command deployment, service dependency management |

---

## 3. Service Details

### 3.1 Broker Service

**Responsibility**: Data distribution only (no processing, per neutrality constraint).

**Startup sequence**:
1. Fetch sensor list from simulator (`GET /api/devices/`)
2. Open WebSocket connection to each sensor
3. Accept subscriber connections from processors

**Fan-out strategy**: Broadcast model — every measurement is sent to every connected processor.

**Reconnection**: If a sensor WebSocket drops, the broker reconnects with exponential backoff (2s intervals).

**Endpoints**:
- `GET /health` — Service health with subscriber count
- `GET /sensors` — Cached sensor metadata
- `WS /ws/subscribe` — Processor subscription endpoint

### 3.2 Processor Service

**Responsibility**: Signal analysis, event classification, and persistence.

**Processing pipeline**:
1. Receive measurement from broker
2. Append to sensor's sliding window (deque, max 256 samples)
3. Every 64 samples, trigger FFT analysis:
   - Apply Hanning window function
   - Compute real FFT via `numpy.fft.rfft`
   - Normalize magnitudes
   - Find dominant frequency (highest peak above 0.5 Hz)
   - Check amplitude threshold AND signal-to-noise ratio
   - Classify event based on frequency bands
4. Generate deduplication key: `{sensor_id}_{event_type}_{time_bucket}`
5. INSERT with ON CONFLICT DO NOTHING

**Control stream handling**: Each replica connects to `GET /api/control` (SSE). On receiving `{"command": "SHUTDOWN"}`, the process terminates immediately via `os._exit(1)`.

**Endpoints**:
- `GET /health` — Replica status
- `GET /api/events` — Query events with filters and pagination
- `GET /api/events/stream` — SSE stream of newly detected events
- `GET /api/sensors` — Tracked sensors and window status
- `GET /api/stats` — Event statistics

### 3.3 Gateway Service

**Responsibility**: Single entry point, load balancing, fault tolerance.

**Health checking**: Every 5 seconds, pings each processor's `/health` endpoint. Unreachable or non-200 processors are removed from the routing pool.

**Load balancing**: Round-robin across healthy processors.

**Failure handling**: Returns HTTP 503 if no healthy processors are available.

**Endpoints** (proxied):
- `GET /health` — Gateway health + replica counts
- `GET /api/events` — Proxied to healthy processor
- `GET /api/events/stream` — SSE proxy with auto-failover
- `GET /api/sensors` — Proxied to healthy processor
- `GET /api/stats` — Proxied to healthy processor
- `GET /api/replicas` — Replica health status (gateway-native)

### 3.4 Frontend Dashboard

**Technology**: React 18 SPA served by Nginx.

**Features**:
- Real-time event table with auto-refresh (2s polling)
- Live event feed via SSE
- Event filtering by sensor and type
- Pagination for historical browsing
- Replica health status panel
- Sensor overview panel
- Summary statistics cards
- System-wide status indicator (Operational/Degraded/Down)

**API proxying**: Nginx forwards `/api/*` and `/health` requests to the gateway, avoiding CORS issues.

---

## 4. Deduplication Strategy

Since all 3 replicas receive the same data and run the same FFT analysis, they will detect the same events. To prevent duplicates:

1. **Time bucketing**: Detection timestamps are floored to 5-second intervals.
2. **Composite key**: `event_id = {sensor_id}_{event_type}_{time_bucket}`
3. **DB constraint**: `UNIQUE(event_id)` on the `detected_events` table.
4. **Idempotent insert**: `INSERT ... ON CONFLICT (event_id) DO NOTHING`

The first replica to insert wins; others silently skip. The `replica_id` field records which replica stored the event.

---

## 5. FFT Analysis Details

### Window Configuration
- **Window size**: 256 samples (12.8 seconds at 20 Hz)
- **Analysis interval**: Every 64 new samples (~3.2 seconds)
- **Window function**: Hanning (reduces spectral leakage)
- **FFT method**: `numpy.fft.rfft` (real-input FFT, efficient)

### Frequency Resolution
- Sampling rate: 20 Hz
- Nyquist frequency: 10 Hz
- Frequency resolution: 20/256 = 0.078 Hz

### Detection Criteria
Both conditions must be met:
1. Peak FFT magnitude > `AMPLITUDE_THRESHOLD` (default: 0.5)
2. Signal-to-noise ratio > `SNR_THRESHOLD` (default: 4.0)

### Classification Bands
| Event Type | Frequency Range |
|------------|----------------|
| Earthquake | 0.5 <= f < 3.0 Hz |
| Conventional Explosion | 3.0 <= f < 8.0 Hz |
| Nuclear-like Event | f >= 8.0 Hz |

---

## 6. Database Schema

```sql
CREATE TABLE detected_events (
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
```

**Indexes**: `sensor_id`, `event_type`, `detected_at DESC` for efficient querying.

---

## 7. Docker Configuration

### Services
| Service | Build Context | Image | Replicas |
|---------|--------------|-------|----------|
| simulator | - | `seismic-signal-simulator:multiarch_v1` | 1 |
| postgres | - | `postgres:16-alpine` | 1 |
| broker | `./broker` | Built from Dockerfile | 1 |
| processor-1 | `./processor` | Built from Dockerfile | 1 |
| processor-2 | `./processor` | Built from Dockerfile | 1 |
| processor-3 | `./processor` | Built from Dockerfile | 1 |
| gateway | `./gateway` | Built from Dockerfile | 1 |
| frontend | `./frontend` | Built from Dockerfile (multi-stage) | 1 |

### Startup Order
1. `simulator` + `postgres` (parallel, with healthchecks)
2. `broker` (waits for simulator healthy)
3. `processor-1`, `processor-2`, `processor-3` (wait for postgres + broker healthy)
4. `gateway` (waits for processors started)
5. `frontend` (waits for gateway started)

### How to Run
```bash
# Load the simulator image (one time)
docker load -i seismic-signal-simulator-oci.tar

# Start everything
cd source/
docker compose up --build
```

The dashboard will be available at **http://localhost:3000**.

---

## 8. API Endpoints Summary

### Gateway (port 8081) / Frontend (port 3000, via nginx proxy)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Gateway health status |
| GET | `/api/events?limit=&offset=&sensor_id=&event_type=&since=` | Query detected events |
| GET | `/api/events/stream` | SSE stream of new events |
| GET | `/api/sensors` | List tracked sensors |
| GET | `/api/stats` | Event statistics summary |
| GET | `/api/replicas` | Processing replica health status |

### Broker (port 9000)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Broker health |
| GET | `/sensors` | Sensor metadata |
| WS | `/ws/subscribe` | Processor subscription |

---

## 9. Fault Tolerance Demo

To demonstrate fault tolerance:

1. Start the system: `docker compose up --build`
2. Wait for events to appear in the dashboard
3. Manually kill a processor: `docker compose stop processor-1`
4. Observe: dashboard shows "Degraded" status, but events continue being detected
5. Restart the processor: `docker compose start processor-1`
6. Observe: system returns to "Operational" status

Alternatively, the simulator's auto-shutdown will randomly kill processors via the SSE control stream. The `restart: always` policy ensures they recover automatically.
