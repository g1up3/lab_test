# SYSTEM DESCRIPTION:

CAMERA CAFE is a distributed seismic intelligence monitoring platform designed to ingest, classify, and visualize seismic signals in real time.
The system receives measurements from a provided simulator container, distributes them through a neutral-region broker, analyzes them in replicated processor services with FFT, and stores detected events in PostgreSQL.
A gateway service exposes a single API entrypoint for the dashboard and applies health-checked round-robin routing across live processor replicas.
The architecture is fault-tolerant and aligned with the exam constraint that neutral-region services must only route/forward data and never perform intelligence processing.

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
