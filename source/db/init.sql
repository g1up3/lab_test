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
CREATE INDEX IF NOT EXISTS idx_events_time_bucket ON detected_events(sensor_id, event_type, time_bucket);
