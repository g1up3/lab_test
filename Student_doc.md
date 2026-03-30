# SYSTEM DESCRIPTION:

CAMERA CAFE is a seismic intelligence monitoring platform composed of containerized services.
A provided simulator emits real-time seismic streams and control events. A broker in the neutral region only forwards sensor data to three processor replicas.
Processors execute FFT-based analysis, classify events, and persist unique detections into PostgreSQL.
A gateway provides a single API entrypoint and applies health-checked round-robin load balancing over live replicas.
A frontend dashboard provides real-time monitoring and historical inspection.

The architecture enforces the exam policy for neutral infrastructure:
- Neutral/routing layer: broker, gateway, frontend
- Processing/data layer: simulator, processor replicas, PostgreSQL


# USER STORIES:

1) As an Operator, I want the system to automatically discover simulator devices so that all sensors are ingested without manual configuration.
2) As a Broker service, I want to connect to every device WebSocket so that I can receive all live measurements.
3) As a Processor replica, I want to subscribe to the broker stream so that I can analyze all sensor data.
4) As a Processor replica, I want to maintain per-sensor sliding windows so that FFT can run on recent samples.
5) As a Processor replica, I want to run FFT analysis periodically so that I can detect dominant frequencies.
6) As a Processor replica, I want to classify low-frequency dominant signals as earthquakes so that natural threats are identified.
7) As a Processor replica, I want to classify mid-frequency dominant signals as conventional explosions so that conventional threats are identified.
8) As a Processor replica, I want to classify high-frequency dominant signals as nuclear-like so that critical threats are identified.
9) As a Processor replica, I want to apply amplitude and SNR thresholds so that random noise is filtered out.
10) As a Processor replica, I want to deduplicate detected events with a deterministic key so that duplicates across replicas are not stored.
11) As a Processor replica, I want to listen continuously to the simulator SSE control stream so that failure commands are received immediately.
12) As a Processor replica, I want to terminate immediately on {"command":"SHUTDOWN"} so that datacenter failure simulation is realistic.
13) As the platform, I want Docker restart policy to recover failed replicas so that service continuity is preserved.
14) As the Gateway, I want to health-check processor replicas frequently so that dead replicas are removed quickly.
15) As the Gateway, I want round-robin routing only over healthy replicas so that request handling remains balanced and resilient.
16) As an Analyst, I want to query events with filters and pagination so that I can inspect historical activity.
17) As an Analyst, I want real-time event updates so that I can monitor live seismic activity.
18) As an Operator, I want to see replica health status so that I can detect degraded conditions.
19) As an Operator, I want a single dashboard endpoint so that operations are centralized.
20) As an Instructor, I want one-command startup with docker compose so that system evaluation is reproducible.
21) As an Instructor, I want simulator env vars and 8080:8080 mapping set in compose so that auto-shutdown tests work correctly.
22) As an Examiner, I want neutral-region routing separated from processing/data services so that policy constraints are clearly satisfied.
23) As an Analyst, I want to filter events by sensor so that I can isolate activity in a specific region.
24) As an Analyst, I want to filter events by event type so that I can focus on one threat category at a time.
25) As an Operator, I want to inspect per-replica health metrics so that I can diagnose partial failures quickly.
26) As an Operator, I want to access a live SSE feed of detected events so that I can react to critical signals immediately.
27) As a Gateway, I want immediate failover if a replica request fails so that client calls remain available.
28) As a Gateway, I want to return 503 when no replicas are healthy so that the system state is explicit to clients.
29) As a Processor, I want to reconnect automatically to broker and control stream after restart so that analysis resumes without manual intervention.
30) As an Instructor, I want reproducible startup order with health-based dependencies so that grading is deterministic.


# CONTAINERS:

## CONTAINER_NAME: Simulator

### DESCRIPTION:
Provided container image that generates sensor streams and fault-injection control commands.

### USER STORIES:
1, 2, 7, 8, 14

### PORTS:
8080:8080

### DESCRIPTION:
The Simulator is configured in docker-compose with contract variables and is reachable at localhost:8080.

### PERSISTANCE EVALUATION
The Simulator does not require persistent storage for this project.

### EXTERNAL SERVICES CONNECTIONS
The Simulator does not connect to external services.

### MICROSERVICES:

#### MICROSERVICE: simulator-api
- TYPE: backend
- DESCRIPTION: Emits seismic measurements and control events.
- PORTS: 8080
- TECHNOLOGICAL SPECIFICATION:
  - Image: seismic-signal-simulator:multiarch_v1
  - Compose variables:
    - SAMPLING_RATE_HZ=20
    - AUTO_SHUTDOWN_ENABLED=true
    - AUTO_SHUTDOWN_MIN_SECONDS=30
    - AUTO_SHUTDOWN_MAX_SECONDS=90
- ENDPOINTS:

	| HTTP METHOD | URL | Description | User Stories |
	| ----------- | --- | ----------- | ------------ |
	| GET | /health | Runtime config and health | 14 |
	| GET | /api/devices/ | Device discovery | 1 |
	| WS | /api/device/{sensor_id}/ws | Sensor stream | 2 |
	| GET | /api/control | SSE control stream | 7, 8 |
	| POST | /api/admin/shutdown | Manual SHUTDOWN trigger | 8 |


## CONTAINER_NAME: Broker

### DESCRIPTION:
Neutral-region forwarding service. It consumes simulator streams and broadcasts measurements to all processors.

### USER STORIES:
2, 3, 15

### PORTS:
9000:9000

### DESCRIPTION:
The Broker performs no intelligence processing and no persistence. It only routes data.

### PERSISTANCE EVALUATION
The Broker does not require persistent storage.

### EXTERNAL SERVICES CONNECTIONS
The Broker connects to:
- Simulator endpoints for discovery and per-device streams
- Processor subscribers via internal WebSocket

### MICROSERVICES:

#### MICROSERVICE: broker
- TYPE: backend
- DESCRIPTION: WebSocket fan-out distributor with sensor metadata enrichment.
- PORTS: 9000
- TECHNOLOGICAL SPECIFICATION:
  - Python 3.12, FastAPI, websockets, httpx
- ENDPOINTS:

	| HTTP METHOD | URL | Description | User Stories |
	| ----------- | --- | ----------- | ------------ |
	| GET | /health | Service health and counters | 15 |
	| GET | /sensors | Cached sensor metadata | 1 |
	| WS | /ws/subscribe | Processor subscription | 3 |


## CONTAINER_NAME: Processor-Cluster

### DESCRIPTION:
Three processing replicas executing FFT analysis, event classification, dedup-safe persistence, and control-stream shutdown handling.

### USER STORIES:
3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13

### PORTS:
9001 (internal Docker network, one service per replica)

### DESCRIPTION:
Each replica receives the full broker stream, analyzes independently, and exits immediately when receiving SHUTDOWN.
Docker restart policy provides automatic recovery.

### PERSISTANCE EVALUATION
Processors do not keep local persistent state. Persistent records are stored in PostgreSQL.

### EXTERNAL SERVICES CONNECTIONS
Processors connect to:
- Broker stream: ws://broker:9000/ws/subscribe
- Simulator control SSE: GET /api/control
- PostgreSQL for event storage

### MICROSERVICES:

#### MICROSERVICE: processor
- TYPE: backend
- DESCRIPTION: FFT-based event detector and persistence service.
- PORTS: 9001 (internal)
- TECHNOLOGICAL SPECIFICATION:
  - Python 3.12, FastAPI, NumPy, asyncpg, websockets, httpx
  - Runtime parameters:
    - WINDOW_SIZE=128
    - ANALYZE_EVERY=32
    - MIN_ANALYSIS_FREQ_HZ=0.5
    - AMPLITUDE_THRESHOLD=0.01
    - SNR_THRESHOLD=1.2
    - TIME_BUCKET_SECONDS=5
- ENDPOINTS:

	| HTTP METHOD | URL | Description | User Stories |
	| ----------- | --- | ----------- | ------------ |
	| GET | /health | Replica health and counters | 13 |
	| GET | /api/events | Historical events query | 11 |
	| GET | /api/events/stream | SSE live event stream | 12 |
	| GET | /api/sensors | Tracked sensors overview | 4 |
	| GET | /api/stats | Aggregated stats | 11 |


## CONTAINER_NAME: Gateway

### DESCRIPTION:
Neutral-region API entrypoint with health-checked round-robin load balancing.

### USER STORIES:
9, 10, 11, 12, 13

### PORTS:
8081:8081

### DESCRIPTION:
Gateway forwards requests to healthy processors, removes dead replicas quickly, and performs failover.

### PERSISTANCE EVALUATION
Gateway logic is stateless for routing; metadata storage uses PostgreSQL.

### EXTERNAL SERVICES CONNECTIONS
Gateway connects to:
- Processor replicas (internal HTTP)
- PostgreSQL (API keys and audit logs)

### MICROSERVICES:

#### MICROSERVICE: gateway
- TYPE: middleware/backend
- DESCRIPTION: Single entrypoint with resilient routing and role-based API access.
- PORTS: 8081
- TECHNOLOGICAL SPECIFICATION:
  - Python 3.12, FastAPI, httpx, asyncpg
  - Health checks: interval 1s, timeout 1.5s
- ENDPOINTS:

	| HTTP METHOD | URL | Description | User Stories |
	| ----------- | --- | ----------- | ------------ |
	| GET | /health | Gateway status and healthy count | 9 |
	| GET | /api/events | Proxied events query | 11 |
	| GET | /api/events/stream | Proxied SSE stream | 12 |
	| GET | /api/sensors | Proxied sensors endpoint | 11 |
	| GET | /api/stats | Proxied stats endpoint | 11 |
	| GET | /api/replicas | Replica liveness snapshot | 13 |


## CONTAINER_NAME: Frontend

### DESCRIPTION:
Operator dashboard for live monitoring, filters, and replica health visibility.

### USER STORIES:
11, 12, 13

### PORTS:
3000:80

### DESCRIPTION:
React SPA served by Nginx; consumes only Gateway APIs.

### PERSISTANCE EVALUATION
Frontend does not require a database.

### EXTERNAL SERVICES CONNECTIONS
Frontend connects to Gateway endpoints.

### MICROSERVICES:

#### MICROSERVICE: dashboard-ui
- TYPE: frontend
- DESCRIPTION: SPA monitoring interface for operators.
- PORTS: 3000
- PAGES:

	| Name | Description | Related Microservice | User Stories |
	| ---- | ----------- | -------------------- | ------------ |
	| Dashboard | Real-time table, filters, stats, replica health | gateway | 11, 12, 13 |


## CONTAINER_NAME: PostgreSQL

### DESCRIPTION:
Persistent database for deduplicated seismic events and gateway metadata.

### USER STORIES:
6, 11

### PORTS:
5432:5432

### DESCRIPTION:
Stores detected events with unique event_id and supports query endpoints.

### PERSISTANCE EVALUATION
PostgreSQL requires persistent storage and uses Docker volume postgres_data.

### EXTERNAL SERVICES CONNECTIONS
PostgreSQL does not connect to external services.

### MICROSERVICES:

#### MICROSERVICE: postgres
- TYPE: database
- DESCRIPTION: ACID storage for events and gateway metadata.
- PORTS: 5432
- DB STRUCTURE:

	**_detected_events_** : | **_id_** | event_id (UNIQUE) | sensor_id | sensor_name | region | event_type | dominant_frequency | magnitude | detected_at | time_bucket | replica_id | created_at |


## NETWORK TOPOLOGY (POLICY COMPLIANCE)

- camera_cafe_neutral_region: broker, gateway, frontend
- camera_cafe_processing_region: simulator, processor-1, processor-2, processor-3, postgres
- Boundary services: broker and gateway are the only allowed bridges between neutral and processing layers.

This guarantees that FFT analysis, classification, and persistence are outside the neutral routing layer.
