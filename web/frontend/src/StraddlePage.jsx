import { useCallback, useEffect, useState } from 'react'

const API_STRADDLE_MONITOR = '/api/straddle-monitor'
const AUTO_REFRESH_MS = 60_000
const LIVE_THRESHOLD_MS = 90_000

function formatPrice(value, digits = 2) {
  if (value == null) return '--'
  return Number(value).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function formatSignedPrice(value, digits = 2) {
  if (value == null) return '--'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${formatPrice(number, digits)}`
}

function formatSignedPct(value, digits = 2) {
  if (value == null) return '--'
  const pct = Number(value) * 100
  return `${pct > 0 ? '+' : ''}${pct.toFixed(digits)}%`
}

function formatRatio(value) {
  if (value == null) return '--'
  return Number(value).toFixed(3)
}

function formatVolPct(value) {
  if (value == null) return '--'
  return `${(Number(value) * 100).toFixed(2)}%`
}

function formatTimestamp(iso) {
  if (!iso) return '--'
  try {
    const date = new Date(iso)
    return date.toLocaleString('en-US', {
      timeZone: 'America/New_York',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    })
  } catch {
    return iso
  }
}

function formatExpiry(isoDate) {
  if (!isoDate) return '--'
  try {
    const [year, month, day] = isoDate.split('-').map(Number)
    return new Date(year, (month || 1) - 1, day || 1).toLocaleDateString('en-US', {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
    })
  } catch {
    return isoDate
  }
}

function formatTimeLabel(iso) {
  if (!iso) return '--'
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      timeZone: 'America/New_York',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    })
  } catch {
    return iso
  }
}

function getConnectionStatus(snapshot, error) {
  if (error && !snapshot) return 'error'
  if (!snapshot?.updated_at) return null
  const age = Date.now() - new Date(snapshot.updated_at).getTime()
  if (!Number.isFinite(age)) return null
  return age <= LIVE_THRESHOLD_MS ? 'live' : 'stale'
}

function MetricCard({ label, value, detail, tone = 'neutral' }) {
  return (
    <article className={`straddle-card straddle-card-${tone}`}>
      <span className="straddle-card-label">{label}</span>
      <span className="straddle-card-value">{value}</span>
      <span className="straddle-card-detail">{detail}</span>
    </article>
  )
}

function LineChart({ title, points = [] }) {
  const validPoints = points.filter((point) => Number.isFinite(Number(point?.value)))
  if (!validPoints.length) {
    return (
      <section className="straddle-chart-card">
        <div className="straddle-chart-head">
          <h3>{title}</h3>
          <span>No intraday data yet</span>
        </div>
        <div className="straddle-chart-empty">History will populate at 1-minute intervals during market hours.</div>
      </section>
    )
  }

  const values = validPoints.map((point) => Number(point.value))
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const chartWidth = 600
  const chartHeight = 220
  const paddingX = 28
  const paddingTop = 20
  const paddingBottom = 28
  const innerWidth = chartWidth - paddingX * 2
  const innerHeight = chartHeight - paddingTop - paddingBottom
  const polyline = validPoints
    .map((point, index) => {
      const x = paddingX + (index / Math.max(validPoints.length - 1, 1)) * innerWidth
      const y = paddingTop + ((max - Number(point.value)) / range) * innerHeight
      return `${x},${y}`
    })
    .join(' ')
  const lastPoint = validPoints[validPoints.length - 1]

  return (
    <section className="straddle-chart-card">
      <div className="straddle-chart-head">
        <h3>{title}</h3>
        <span>Last {formatPrice(lastPoint.value)}</span>
      </div>
      <svg className="straddle-chart-svg" viewBox={`0 0 ${chartWidth} ${chartHeight}`} role="img" aria-label={title}>
        <line x1={paddingX} y1={chartHeight - paddingBottom} x2={chartWidth - paddingX} y2={chartHeight - paddingBottom} className="straddle-chart-axis-line" />
        <line x1={paddingX} y1={paddingTop} x2={paddingX} y2={chartHeight - paddingBottom} className="straddle-chart-axis-line" />
        <polyline className="straddle-chart-line" points={polyline} />
        <circle
          className="straddle-chart-dot"
          cx={paddingX + innerWidth}
          cy={paddingTop + ((max - Number(lastPoint.value)) / range) * innerHeight}
          r="4"
        />
        <text x={paddingX} y={14} className="straddle-chart-scale">{formatPrice(max)}</text>
        <text x={paddingX} y={chartHeight - 8} className="straddle-chart-scale">{formatPrice(min)}</text>
      </svg>
      <div className="straddle-chart-footer">
        <span>{formatTimeLabel(validPoints[0]?.timestamp)}</span>
        <span>{formatTimeLabel(lastPoint?.timestamp)}</span>
      </div>
    </section>
  )
}

function QuoteCell({ bid, ask }) {
  return (
    <span className="straddle-quote-cell">
      <span>{formatPrice(bid)}</span>
      <span className="straddle-quote-sep">x</span>
      <span>{formatPrice(ask)}</span>
    </span>
  )
}

export default function StraddlePage() {
  const [snapshot, setSnapshot] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [, setTick] = useState(0)

  const fetchMonitor = useCallback(async () => {
    setError(null)
    try {
      const response = await fetch(`${API_STRADDLE_MONITOR}?rows=8`)
      if (!response.ok) {
        const text = await response.text()
        throw new Error(text || `HTTP ${response.status}`)
      }
      const data = await response.json()
      setSnapshot(data)
    } catch (err) {
      setError(err.message || 'Failed to load straddle monitor')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchMonitor()
  }, [fetchMonitor])

  useEffect(() => {
    const intervalId = setInterval(fetchMonitor, AUTO_REFRESH_MS)
    return () => clearInterval(intervalId)
  }, [fetchMonitor])

  useEffect(() => {
    const tickId = setInterval(() => setTick((tick) => tick + 1), 1000)
    return () => clearInterval(tickId)
  }, [])

  useEffect(() => {
    if (typeof document === 'undefined') return
    document.title = 'SPX Straddle Monitor'
  }, [])

  const status = getConnectionStatus(snapshot, error)
  const rows = snapshot?.rows || []
  const history = snapshot?.history || {}
  const updatedAt = snapshot?.updated_at
  const updatedAgo = updatedAt ? Math.max(0, Math.floor((Date.now() - new Date(updatedAt).getTime()) / 1000)) : null

  if (loading && !snapshot) {
    return (
      <main className="straddle-page straddle-loading-state">
        <div className="straddle-shell">
          <h1>SPX Straddle Monitor</h1>
          <p>Loading live SPX straddle data…</p>
        </div>
      </main>
    )
  }

  return (
    <main className="straddle-page">
      <div className="straddle-shell">
        <header className="straddle-hero">
          <div>
            <p className="straddle-kicker">SPX-only monitor</p>
            <h1>SPX Straddle Monitor</h1>
            <p className="straddle-subtitle">Minute-resolution monitor for front SPX expiries with persisted 0DTE and 1DTE intraday history.</p>
          </div>
          <div className="straddle-actions">
            {status && <span className={`status-pill ${status}`}>{status === 'live' ? 'LIVE' : status === 'stale' ? 'STALE' : 'ERROR'}</span>}
            <button type="button" className="btn-refresh" onClick={fetchMonitor}>Refresh</button>
          </div>
        </header>

        {error && <div className="straddle-error-banner">{error}</div>}

        <section className="straddle-card-grid">
          <MetricCard
            label="SPX Spot"
            value={formatPrice(snapshot?.spot)}
            detail={`${formatSignedPrice(snapshot?.spot_change)} · ${formatSignedPct(snapshot?.spot_change_pct)}`}
            tone={snapshot?.spot_change > 0 ? 'positive' : snapshot?.spot_change < 0 ? 'negative' : 'neutral'}
          />
          <MetricCard
            label="Active Strike"
            value={formatPrice(snapshot?.active_strike, 0)}
            detail={rows[0]?.expiration ? `${rows[0].days_to_expiry} DTE · ${formatExpiry(rows[0].expiration)}` : 'Nearest listed SPX strike'}
          />
          <MetricCard
            label="VIX"
            value={formatPrice(snapshot?.vix)}
            detail={`${formatSignedPrice(snapshot?.vix_change)} · ${formatSignedPct(snapshot?.vix_change_pct)}`}
            tone={snapshot?.vix_change > 0 ? 'negative' : snapshot?.vix_change < 0 ? 'positive' : 'neutral'}
          />
          <MetricCard
            label="Status"
            value={updatedAgo == null ? '--' : `${updatedAgo}s ago`}
            detail={`Updated ${formatTimestamp(snapshot?.chain_timestamp || snapshot?.quote_timestamp || snapshot?.updated_at)}`}
          />
        </section>

        <section className="straddle-table-card">
          <div className="straddle-section-head">
            <div>
              <h2>Near-Term Straddles</h2>
              <p>SPX option-chain snapshot updates every 60 seconds.</p>
            </div>
            <span className="straddle-resolution-pill">1m cadence</span>
          </div>
          <div className="straddle-table-wrap">
            <table className="straddle-table">
              <thead>
                <tr>
                  <th>DTE</th>
                  <th>Expiry</th>
                  <th>Strike</th>
                  <th>Call Bid/Ask</th>
                  <th>Put Bid/Ask</th>
                  <th>Straddle</th>
                  <th>Impl Mov</th>
                  <th>P/C Skew</th>
                  <th>IV</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const rowClass = row.days_to_expiry <= 1 ? 'is-front' : row.days_to_expiry <= 3 ? 'is-near' : ''
                  return (
                  <tr key={row.expiration} className={rowClass}>
                    <td>{row.days_to_expiry ?? '--'}</td>
                    <td>{formatExpiry(row.expiration)}</td>
                    <td>{formatPrice(row.strike, 0)}</td>
                    <td><QuoteCell bid={row.call_bid} ask={row.call_ask} /></td>
                    <td><QuoteCell bid={row.put_bid} ask={row.put_ask} /></td>
                    <td className="straddle-table-strong">{formatPrice(row.straddle_mid)}</td>
                    <td>
                      <div className="straddle-impl-move">
                        <span>{formatPrice(row.implied_move_points)}</span>
                        <span>{formatVolPct(row.implied_move_pct)}</span>
                      </div>
                    </td>
                    <td>{formatRatio(row.put_call_skew)}</td>
                    <td>{formatVolPct(row.iv)}</td>
                  </tr>
                  )
                })}
                {!rows.length && (
                  <tr>
                    <td colSpan="9" className="straddle-empty-row">No SPX rows available.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="straddle-chart-grid">
          <LineChart title="Intraday Straddle (0DTE)" points={history['0dte'] || []} />
          <LineChart title="Intraday Straddle (1DTE)" points={history['1dte'] || []} />
        </section>
      </div>
    </main>
  )
}
