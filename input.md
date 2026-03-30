# CAMERA CAFE - Seismic Intelligence Monitoring Platform

## System Overview

CAMERA CAFE is a distributed, fault-tolerant seismic analysis platform designed to ingest, process, classify, and visualize seismic signals in real time. The system operates under the constraint that the command center (neutral region) cannot directly process intelligence data — it can only route and forward requests.

The platform ingests live seismic data from geographically distributed sensors via a provided simulator container. Measurements are fan-out distributed by a custom broker to multiple replicated processing services. Each replica performs sliding-window FFT analysis to detect and classify seismic events (earthquakes, conventional explosions, nuclear-like events). Detected events are persisted in a shared PostgreSQL database with duplicate-safe behavior. A real-time dashboard provides operators with monitoring, historical inspection, and filtering capabilities.

### Key Architectural Decisions

- **Broker in neutral region**: The broker only forwards data (no processing), compliant with neutrality constraints.
- **Replicated processors**: 3 processing replicas for fault tolerance. Each listens to the simulator's SSE control stream and terminates on SHUTDOWN commands.
- **Gateway with health checks**: Routes traffic to healthy replicas, automatically excluding failed ones.
- **Deduplication via time bucketing**: Since all replicas detect the same events, a composite unique key `(sensor_id, event_type, time_bucket)` ensures only one copy is stored.

---

## User Stories

### US-01: Sensor Discovery
**As** a system operator, **I want** the platform to automatically discover all available seismic sensors from the simulator on startup, **so that** I don't need to manually configure sensor endpoints.

**Acceptance Criteria:**
- The broker fetches the sensor list from `GET /api/devices/` on startup.
- All sensors are displayed in the dashboard.
- New sensors are available without restarting the system.

---

### US-02: Real-Time Data Ingestion
**As** the broker service, **I want** to connect to each sensor's WebSocket stream and receive measurements in real time, **so that** no data is lost.

**Acceptance Criteria:**
- WebSocket connections are established to all sensors.
- Measurements are received at the configured sampling rate (20 Hz).
- Connection drops are detected and reconnected automatically.

---

### US-03: Fan-Out Distribution
**As** a processing replica, **I want** to receive all sensor measurements from the broker via WebSocket, **so that** I can perform independent analysis.

**Acceptance Criteria:**
- All connected replicas receive every measurement (broadcast model).
- The broker adds sensor metadata (sensor_id, name, region) to each message.
- Subscriber disconnections are handled gracefully.

---

### US-04: Sliding Window Maintenance
**As** a processing replica, **I want** to maintain an in-memory sliding window of the last 256 samples for each sensor, **so that** I have enough data for frequency analysis.

**Acceptance Criteria:**
- Each sensor has an independent window of configurable size (default: 256 samples).
- Old samples are discarded as new ones arrive (FIFO).
- Analysis triggers every 64 new samples.

---

### US-05: FFT-Based Frequency Analysis
**As** a processing replica, **I want** to apply a Discrete Fourier Transform on each sensor's sliding window, **so that** I can identify dominant frequency components.

**Acceptance Criteria:**
- A Hanning window is applied before FFT to reduce spectral leakage.
- The magnitude spectrum is computed from the FFT result.
- The dominant frequency (highest magnitude above 0.5 Hz) is identified.

---

### US-06: Event Classification - Earthquake
**As** a processing replica, **I want** to classify a detected signal as an earthquake when the dominant frequency is between 0.5 Hz and 3.0 Hz, **so that** command can assess natural seismic threats.

**Acceptance Criteria:**
- Dominant frequency in range [0.5, 3.0) Hz triggers `earthquake` classification.
- The event is persisted with frequency and magnitude data.

---

### US-07: Event Classification - Conventional Explosion
**As** a processing replica, **I want** to classify a detected signal as a conventional explosion when the dominant frequency is between 3.0 Hz and 8.0 Hz, **so that** command can assess conventional military threats.

**Acceptance Criteria:**
- Dominant frequency in range [3.0, 8.0) Hz triggers `conventional_explosion` classification.
- The event is persisted with frequency and magnitude data.

---

### US-08: Event Classification - Nuclear-Like Event
**As** a processing replica, **I want** to classify a detected signal as a nuclear-like event when the dominant frequency is 8.0 Hz or above, **so that** command can initiate highest-level protocols.

**Acceptance Criteria:**
- Dominant frequency >= 8.0 Hz triggers `nuclear_like` classification.
- The event is persisted with frequency and magnitude data.

---

### US-09: Amplitude Thresholding
**As** a processing replica, **I want** to apply amplitude and signal-to-noise ratio thresholds before classifying an event, **so that** noise is not misclassified as a seismic event.

**Acceptance Criteria:**
- Events are only detected if peak FFT magnitude exceeds the configured threshold.
- Events are only detected if the signal-to-noise ratio exceeds the configured threshold.
- Both thresholds are configurable via environment variables.

---

### US-10: Duplicate-Safe Event Persistence
**As** a processing replica, **I want** to store detected events in PostgreSQL with deduplication, **so that** multiple replicas detecting the same event don't create duplicate records.

**Acceptance Criteria:**
- Events are uniquely keyed by `(sensor_id, event_type, time_bucket)`.
- Time bucket rounds the detection time to 5-second intervals.
- `INSERT ... ON CONFLICT DO NOTHING` ensures idempotent writes.

---

### US-11: SSE Control Stream Listening
**As** a processing replica, **I want** to listen to the simulator's SSE control stream, **so that** I can receive shutdown commands.

**Acceptance Criteria:**
- Each replica maintains a persistent SSE connection to `GET /api/control`.
- Heartbeat events are acknowledged to keep the connection alive.
- Connection drops are detected and reconnected automatically.

---

### US-12: Graceful Shutdown on Command
**As** a processing replica, **I want** to immediately terminate when I receive a SHUTDOWN command, **so that** datacenter destruction is simulated realistically.

**Acceptance Criteria:**
- On receiving `{"command": "SHUTDOWN"}`, the replica terminates with `os._exit(1)`.
- Docker's `restart: always` policy restarts the replica automatically.
- Other replicas continue operating during the restart.

---

### US-13: Automatic Replica Recovery
**As** the system, **I want** failed processing replicas to automatically restart, **so that** the system recovers from failures without manual intervention.

**Acceptance Criteria:**
- Docker Compose `restart: always` policy is configured for all processor services.
- Restarted replicas reconnect to the broker and control stream.
- The gateway detects the recovered replica within one health check interval.

---

### US-14: Gateway Health Checks
**As** the gateway, **I want** to periodically health-check all processing replicas, **so that** I can route requests only to healthy ones.

**Acceptance Criteria:**
- Health checks run every 5 seconds (configurable).
- A replica is marked unhealthy if its `/health` endpoint is unreachable or returns non-200.
- Unhealthy replicas are excluded from the routing pool.

---

### US-15: Load-Balanced Request Routing
**As** the gateway, **I want** to distribute incoming API requests across healthy replicas using round-robin, **so that** load is balanced evenly.

**Acceptance Criteria:**
- Requests are forwarded to healthy replicas in round-robin order.
- If no replicas are healthy, the gateway returns HTTP 503.
- The routing pool updates automatically as replicas come and go.

---

### US-16: Real-Time Event Dashboard
**As** a system operator, **I want** a web dashboard that displays detected seismic events in real time, **so that** I can monitor the situation continuously.

**Acceptance Criteria:**
- The dashboard polls the API every 2 seconds for new events.
- New events appear at the top of the table.
- A live indicator shows the dashboard is actively updating.

---

### US-17: Historical Event Inspection
**As** an analyst, **I want** to browse the full history of detected events with pagination, **so that** I can review past activity.

**Acceptance Criteria:**
- Events are displayed in a sortable table with columns: time, sensor, region, type, frequency, magnitude, replica.
- Pagination allows browsing all events (30 per page).
- Total event count is displayed.

---

### US-18: Event Filtering by Sensor
**As** an analyst, **I want** to filter events by sensor, **so that** I can focus on a specific geographic area.

**Acceptance Criteria:**
- A dropdown lists all sensors that have detected events.
- Selecting a sensor filters the table to only show events from that sensor.
- Filters can be cleared to show all events again.

---

### US-19: Event Filtering by Type
**As** an analyst, **I want** to filter events by type (earthquake, explosion, nuclear-like), **so that** I can assess specific threat categories.

**Acceptance Criteria:**
- A dropdown lists the three event types.
- Selecting a type filters the table accordingly.
- Filters can be combined with sensor filter.

---

### US-20: Replica Status Monitoring
**As** a system operator, **I want** to see the health status of all processing replicas in the dashboard, **so that** I know if the system is degraded.

**Acceptance Criteria:**
- Each replica is shown with a colored status indicator (green=healthy, red=unhealthy, gray=unreachable).
- The header shows overall system status: "Operational", "Degraded", or "Down".
- Replica event counts are displayed.

---

### US-21: Event Statistics Summary
**As** a system operator, **I want** to see summary statistics (total events, events by type, recent events), **so that** I have a quick overview of the situation.

**Acceptance Criteria:**
- Stat cards show total events, events in last 5 minutes, and counts by type.
- Statistics update automatically every 2 seconds.

---

### US-22: Sensor Overview
**As** a system operator, **I want** to see a list of all sensors with their category and region, **so that** I understand the sensor deployment.

**Acceptance Criteria:**
- Sensors are listed in the sidebar with name, region, and category badge (field/datacenter).
- Sensor list updates periodically.

---

### US-23: SSE Live Event Stream
**As** the frontend, **I want** to receive new events via Server-Sent Events, **so that** I can update the live feed without polling.

**Acceptance Criteria:**
- The frontend connects to `/api/events/stream` SSE endpoint.
- New events appear instantly in the Live Feed panel.
- The SSE connection reconnects on failure.

---

### US-24: Docker Compose One-Command Startup
**As** an instructor, **I want** to start the entire system with `docker compose up`, **so that** no manual setup is required.

**Acceptance Criteria:**
- All services (simulator, postgres, broker, 3 processors, gateway, frontend) start with a single command.
- Service dependencies are correctly ordered via `depends_on` with health checks.
- The database schema is created automatically via init script.

---

### US-25: System Resilience Under Partial Failure
**As** a system operator, **I want** the system to continue operating when one or more processing replicas are shut down, **so that** intelligence is not interrupted.

**Acceptance Criteria:**
- When a replica is shut down, the gateway detects the failure within 5 seconds.
- Remaining replicas continue detecting and persisting events.
- The dashboard shows degraded status but remains functional.
- When the replica recovers, it rejoins the system automatically.

---

## Standard Event Schema

```json
{
  "event_id": "sensor-01_earthquake_2026-03-28T12:00:00+00:00",
  "sensor_id": "sensor-01",
  "sensor_name": "Alpine Monitoring Station",
  "region": "Alpine Region",
  "event_type": "earthquake",
  "dominant_frequency": 1.523,
  "magnitude": 3.214,
  "detected_at": "2026-03-28T12:00:02.150000+00:00",
  "time_bucket": "2026-03-28T12:00:00+00:00",
  "replica_id": "processor-1",
  "created_at": "2026-03-28T12:00:02.200000+00:00"
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | string | Unique identifier: `{sensor_id}_{event_type}_{time_bucket}` |
| `sensor_id` | string | Identifier of the sensor that detected the event |
| `sensor_name` | string | Human-readable sensor name |
| `region` | string | Logical geographic region of the sensor |
| `event_type` | enum | One of: `earthquake`, `conventional_explosion`, `nuclear_like` |
| `dominant_frequency` | float | Dominant frequency component in Hz |
| `magnitude` | float | Peak FFT magnitude (normalized) |
| `detected_at` | ISO-8601 | UTC timestamp when the event was detected |
| `time_bucket` | ISO-8601 | Detection time rounded to 5s for deduplication |
| `replica_id` | string | ID of the processing replica that first persisted the event |
| `created_at` | ISO-8601 | Database insertion timestamp |

## Rule Model

### Classification Rules

| Rule | Condition | Classification |
|------|-----------|----------------|
| R1 | 0.5 <= dominant_freq < 3.0 Hz | `earthquake` |
| R2 | 3.0 <= dominant_freq < 8.0 Hz | `conventional_explosion` |
| R3 | dominant_freq >= 8.0 Hz | `nuclear_like` |

### Detection Thresholds

| Parameter | Default | Description |
|-----------|---------|-------------|
| `WINDOW_SIZE` | 256 | Samples in sliding window (12.8s at 20Hz) |
| `ANALYZE_EVERY` | 64 | Samples between analyses (~3.2s) |
| `AMPLITUDE_THRESHOLD` | 0.5 | Minimum FFT peak magnitude |
| `SNR_THRESHOLD` | 4.0 | Minimum signal-to-noise ratio |
| `TIME_BUCKET_SECONDS` | 5 | Deduplication time granularity |

### Deduplication Strategy

Events are deduplicated using a composite key: `{sensor_id}_{event_type}_{time_bucket}`. The time bucket is computed by flooring the detection timestamp to the nearest `TIME_BUCKET_SECONDS` interval. PostgreSQL's `INSERT ... ON CONFLICT DO NOTHING` ensures that only the first replica to persist an event succeeds; all others silently skip the duplicate.
