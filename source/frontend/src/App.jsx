import { useState, useEffect, useCallback, useRef } from 'react'
import { geoNaturalEarth1, geoPath, geoGraticule10 } from 'd3-geo'
import { feature } from 'topojson-client'
import worldAtlas from 'world-atlas/countries-110m.json'

const API_BASE = '/api'
const POLL_INTERVAL = 2000

function formatTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    + '.' + String(d.getMilliseconds()).padStart(3, '0')
}

function formatDate(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleDateString('en-GB') + ' ' + formatTime(iso)
}

function EventBadge({ type }) {
  const labels = {
    earthquake: 'Earthquake',
    conventional_explosion: 'Explosion',
    nuclear_like: 'Nuclear-like',
  }
  return <span className={`event-badge ${type}`}>{labels[type] || type}</span>
}

function SensorGlobe({ sensors, activeSensorId }) {
  const width = 620
  const height = 340
  const projection = geoNaturalEarth1()
    .fitExtent([[16, 16], [width - 16, height - 16]], { type: 'Sphere' })
  const path = geoPath(projection)

  const countries = feature(worldAtlas, worldAtlas.objects.countries)
  const graticule = geoGraticule10()

  const plotSensors = sensors
    .filter(s => s?.coordinates?.latitude != null && s?.coordinates?.longitude != null)
    .map(s => {
      const point = projection([s.coordinates.longitude, s.coordinates.latitude])
      if (!point) return null
      return {
        ...s,
        x: point[0],
        y: point[1],
      }
    })
    .filter(Boolean)

  if (plotSensors.length === 0) {
    return <div className="empty-state">No coordinate data available for globe rendering.</div>
  }

  return (
    <div className="globe-wrap">
      <svg className="globe-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="World atlas sensor map">
        <defs>
          <linearGradient id="atlasSea" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#1f5f8b" />
            <stop offset="45%" stopColor="#113b5d" />
            <stop offset="100%" stopColor="#0a243b" />
          </linearGradient>
        </defs>

        <path d={path({ type: 'Sphere' })} className="atlas-sea" fill="url(#atlasSea)" />
        <path d={path(graticule)} className="atlas-graticule" />
        <g className="atlas-countries">
          {countries.features.map((country, i) => (
            <path key={country.id || i} d={path(country)} />
          ))}
        </g>

        {plotSensors.map((sensor) => {
          const isActive = activeSensorId && sensor.id === activeSensorId
          const markerClass = `globe-marker ${sensor.category || 'field'}${isActive ? ' active' : ''}`
          return (
            <g key={sensor.id} className={markerClass} transform={`translate(${sensor.x}, ${sensor.y})`}>
              <circle className="globe-marker-pulse" r="6" />
              <circle className="globe-marker-core" r="3.2" />
              <title>{`${sensor.name || sensor.id} (${sensor.region || 'Unknown'})`}</title>
            </g>
          )
        })}
      </svg>

      <div className="globe-legend">
        <span><span className="legend-dot field"></span>Field sensors</span>
        <span><span className="legend-dot datacenter"></span>Datacenter sensors</span>
      </div>
    </div>
  )
}

export default function App() {
  const [events, setEvents] = useState([])
  const [totalEvents, setTotalEvents] = useState(0)
  const [stats, setStats] = useState(null)
  const [replicas, setReplicas] = useState(null)
  const [sensors, setSensors] = useState([])
  const [gatewayHealth, setGatewayHealth] = useState(null)
  const [apiKey, setApiKey] = useState(() => localStorage.getItem('cameraCafeApiKey') || 'admin-camera-cafe-dev')

  // Filters
  const [filterSensor, setFilterSensor] = useState('')
  const [filterType, setFilterType] = useState('')
  const [filterRegion, setFilterRegion] = useState('')
  const [page, setPage] = useState(0)
  const pageSize = 30

  // SSE for real-time
  const sseRef = useRef(null)
  const [liveEvents, setLiveEvents] = useState([])

  useEffect(() => {
    localStorage.setItem('cameraCafeApiKey', apiKey)
  }, [apiKey])

  const authHeaders = useCallback(() => {
    if (!apiKey.trim()) return {}
    return { 'X-API-Key': apiKey.trim() }
  }, [apiKey])

  const apiUrl = useCallback((path, params = null) => {
    const query = new URLSearchParams(params || {})
    if (apiKey.trim()) query.set('api_key', apiKey.trim())
    const q = query.toString()
    if (!q) return `${API_BASE}${path}`
    return `${API_BASE}${path}?${q}`
  }, [apiKey])

  const fetchEvents = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: pageSize, offset: page * pageSize })
      if (filterSensor) params.set('sensor_id', filterSensor)
      if (filterType) params.set('event_type', filterType)
      if (filterRegion) params.set('region', filterRegion)
      const resp = await fetch(apiUrl('/events', Object.fromEntries(params)), { headers: authHeaders() })
      if (resp.ok) {
        const data = await resp.json()
        setEvents(data.events || [])
        setTotalEvents(data.total || 0)
      }
    } catch (e) { /* ignore */ }
  }, [page, filterSensor, filterType, filterRegion, apiUrl, authHeaders])

  const fetchStats = useCallback(async () => {
    try {
      const resp = await fetch(apiUrl('/stats'), { headers: authHeaders() })
      if (resp.ok) setStats(await resp.json())
    } catch (e) { /* ignore */ }
  }, [apiUrl, authHeaders])

  const fetchReplicas = useCallback(async () => {
    try {
      const resp = await fetch(apiUrl('/replicas'), { headers: authHeaders() })
      if (resp.ok) setReplicas(await resp.json())
    } catch (e) { /* ignore */ }
  }, [apiUrl, authHeaders])

  const fetchSensors = useCallback(async () => {
    try {
      const resp = await fetch(apiUrl('/sensors'), { headers: authHeaders() })
      if (resp.ok) setSensors(await resp.json())
    } catch (e) { /* ignore */ }
  }, [apiUrl, authHeaders])

  const fetchGatewayHealth = useCallback(async () => {
    try {
      const resp = await fetch('/health')
      if (resp.ok) setGatewayHealth(await resp.json())
    } catch (e) { /* ignore */ }
  }, [])

  // Polling
  useEffect(() => {
    fetchEvents()
    fetchStats()
    fetchReplicas()
    fetchSensors()
    fetchGatewayHealth()
    const interval = setInterval(() => {
      fetchEvents()
      fetchStats()
      fetchReplicas()
      fetchGatewayHealth()
    }, POLL_INTERVAL)
    return () => clearInterval(interval)
  }, [fetchEvents, fetchStats, fetchReplicas, fetchGatewayHealth])

  // Slower sensor refresh
  useEffect(() => {
    const interval = setInterval(fetchSensors, 10000)
    return () => clearInterval(interval)
  }, [fetchSensors])

  // SSE connection for live events
  useEffect(() => {
    let cancelled = false

    if (!apiKey.trim()) {
      sseRef.current?.close()
      return () => {}
    }

    function connectSSE() {
      if (cancelled) return
      const sse = new EventSource(apiUrl('/events/stream'))
      sseRef.current = sse
      sse.onmessage = (e) => {
        try {
          const evt = JSON.parse(e.data)
          setLiveEvents(prev => [evt, ...prev].slice(0, 50))
        } catch (err) { /* ignore */ }
      }
      sse.onerror = () => {
        sse.close()
        if (!cancelled) setTimeout(connectSSE, 3000)
      }
    }

    connectSSE()
    return () => { cancelled = true; sseRef.current?.close() }
  }, [apiUrl, apiKey])

  // Reset page when filters change
  useEffect(() => { setPage(0) }, [filterSensor, filterType, filterRegion])

  const healthyCount = replicas?.healthy || 0
  const totalReplicas = replicas?.total || 0
  const systemStatus = healthyCount === 0 ? 'down' : healthyCount < totalReplicas ? 'degraded' : 'healthy'
  const statusLabels = { healthy: 'All Systems Operational', degraded: 'Degraded', down: 'System Down' }

  const uniqueSensors = [...new Set(events.map(e => e.sensor_id).concat(sensors.map(s => s.id)))]
  const uniqueRegions = [...new Set(events.map(e => e.region).concat(sensors.map(s => s.region)).filter(Boolean))]
  const totalPages = Math.ceil(totalEvents / pageSize)
  const lastEventAt = events[0]?.detected_at || liveEvents[0]?.detected_at || null
  const updatedAt = new Date().toISOString()

  return (
    <div className="app">
      <div className="ambient-glow ambient-left"></div>
      <div className="ambient-glow ambient-right"></div>

      <div className="top-bar">
        <span className="top-pill">Telemetry stream live</span>
        <span className="top-meta">Updated {formatTime(updatedAt)}</span>
      </div>

      {/* Header */}
      <div className="header">
        <div>
          <h1>CAMERA CAFE</h1>
          <div className="header-subtitle">Seismic Intelligence Monitoring Platform</div>
          <div className="header-meta">
            <span>Last Event: {lastEventAt ? formatDate(lastEventAt) : 'No events yet'}</span>
            <span>Gateway Uptime: {gatewayHealth?.uptime_seconds ?? '-'}s</span>
          </div>
        </div>
        <div className="system-status">
          <span className={`status-badge ${systemStatus}`}>
            {statusLabels[systemStatus]}
          </span>
          <span className={`status-badge healthy`}>
            {healthyCount}/{totalReplicas} Replicas
          </span>
        </div>
      </div>

      {/* Stats Row */}
      <div className="stats-row">
        <div className="stat-card">
          <div className="label">Total Events</div>
          <div className="value">{stats?.total_events ?? totalEvents}</div>
        </div>
        <div className="stat-card">
          <div className="label">Last 5 min</div>
          <div className="value">{stats?.recent_events_5min ?? '-'}</div>
        </div>
        <div className="stat-card">
          <div className="label">Earthquakes</div>
          <div className="value earthquake">{stats?.by_type?.earthquake ?? 0}</div>
        </div>
        <div className="stat-card">
          <div className="label">Explosions</div>
          <div className="value explosion">{stats?.by_type?.conventional_explosion ?? 0}</div>
        </div>
        <div className="stat-card">
          <div className="label">Nuclear-like</div>
          <div className="value nuclear">{stats?.by_type?.nuclear_like ?? 0}</div>
        </div>
        <div className="stat-card">
          <div className="label">Tracked Sensors</div>
          <div className="value sensors">{sensors.length}</div>
        </div>
      </div>

      {/* Filters */}
      <div className="filters">
        <div className="filter-title">Filter stream</div>
        <select value={filterSensor} onChange={e => setFilterSensor(e.target.value)}>
          <option value="">All Sensors</option>
          {uniqueSensors.sort().map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select value={filterType} onChange={e => setFilterType(e.target.value)}>
          <option value="">All Event Types</option>
          <option value="earthquake">Earthquake</option>
          <option value="conventional_explosion">Conventional Explosion</option>
          <option value="nuclear_like">Nuclear-like</option>
        </select>
        <select value={filterRegion} onChange={e => setFilterRegion(e.target.value)}>
          <option value="">All Regions</option>
          {uniqueRegions.sort().map(r => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
        <button onClick={() => { setFilterSensor(''); setFilterType(''); setFilterRegion(''); setPage(0) }}>
          Clear Filters
        </button>
      </div>

      {/* Content */}
      <div className="content">
        {/* Events Table */}
        <div className="panel">
          <div className="panel-header">
            <span>Detected Events ({totalEvents})</span>
            <span><span className="live-dot"></span>Live</span>
          </div>
          <div className="table-scroll">
            <table className="events-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Sensor</th>
                  <th>Region</th>
                  <th>Type</th>
                  <th>Frequency</th>
                  <th>Magnitude</th>
                  <th>Replica</th>
                </tr>
              </thead>
              <tbody>
                {events.length === 0 ? (
                  <tr>
                    <td colSpan="7">
                      <div className="empty-state">
                        No events detected yet. Waiting for seismic activity...
                      </div>
                    </td>
                  </tr>
                ) : (
                  events.map((evt, i) => (
                    <tr key={evt.event_id || i}>
                      <td>{formatDate(evt.detected_at)}</td>
                      <td>{evt.sensor_name || evt.sensor_id}</td>
                      <td>{evt.region || '-'}</td>
                      <td><EventBadge type={evt.event_type} /></td>
                      <td>{evt.dominant_frequency?.toFixed(2)} Hz</td>
                      <td>{evt.magnitude?.toFixed(3)}</td>
                      <td className="replica-label">{evt.replica_id}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <div className="pagination">
              <button disabled={page === 0} onClick={() => setPage(p => p - 1)}>Previous</button>
              <span>Page {page + 1} of {totalPages}</span>
              <button disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>Next</button>
            </div>
          )}
        </div>

        {/* Sidebar */}
        <div className="sidebar">
          {/* Sensor Globe */}
          <div className="panel">
            <div className="panel-header">Sensor Globe ({sensors.length})</div>
            <SensorGlobe sensors={sensors} activeSensorId={filterSensor || null} />
          </div>

          {/* Live Feed */}
          <div className="panel">
            <div className="panel-header">
              <span><span className="live-dot"></span>Live Feed</span>
            </div>
            <div className="live-feed-scroll">
              {liveEvents.length === 0 ? (
                <div className="empty-state live-empty-state">
                  Waiting for real-time events...
                </div>
              ) : (
                liveEvents.map((evt, i) => (
                  <div key={i} className="live-feed-item">
                    <div className="live-feed-row">
                      <EventBadge type={evt.event_type} />
                      <span className="live-feed-time">{formatTime(evt.detected_at)}</span>
                    </div>
                    <div className="live-feed-sub">{evt.sensor_name || evt.sensor_id} - {evt.dominant_frequency?.toFixed(2)} Hz</div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Replicas */}
          <div className="panel">
            <div className="panel-header">Processing Replicas</div>
            {replicas?.replicas?.map((r, i) => (
              <div className="replica-item" key={i}>
                <span>
                  <span className={`replica-dot ${r.status}`}></span>
                  {r.details?.replica_id || r.url.split('//')[1]?.split(':')[0] || `Replica ${i + 1}`}
                </span>
                <span className="replica-count">
                  {r.status === 'healthy'
                    ? `${r.details?.events_detected ?? 0} events`
                    : r.status}
                </span>
              </div>
            ))}
          </div>

          {/* Sensors */}
          <div className="panel">
            <div className="panel-header">Sensors ({sensors.length})</div>
            <div className="sensor-list">
              {sensors.map((s, i) => (
                <div className="sensor-item" key={i}>
                  <div>
                    <div className="sensor-name">{s.name || s.id}</div>
                    <div className="sensor-region">{s.region}</div>
                  </div>
                  <span className={`sensor-category ${s.category}`}>{s.category}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
