import { useCallback, useEffect, useState } from 'react'

import AppNav from './AppNav'

const API_STRADDLE_MONITOR = '/api/straddle-monitor'
const AUTO_REFRESH_MS = 60_000
const LIVE_THRESHOLD_MS = 90_000
const MONITOR_ROWS = 10

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
  const number = Number(value)
  const sign = number > 0 ? '+' : ''
  return `${sign}${number.toFixed(2)}`
}

function formatVolPct(value, digits = 1) {
  if (value == null) return '--'
  return `${(Number(value) * 100).toFixed(digits)}%`
}

function formatTimestamp(iso) {
  if (!iso) return '--'
  try {
    const date = new Date(iso)
    return date.toLocaleString('en-US', {
      timeZone: 'America/New_York',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  } catch {
    return iso
  }
}

function formatExpiry(isoDate) {
  if (!isoDate) return '--'
  return isoDate
}

function formatSessionDate(isoDate) {
  if (!isoDate) return '--'
  try {
    const [year, month, day] = isoDate.split('-').map(Number)
    return new Date(year, (month || 1) - 1, day || 1).toLocaleDateString('en-US', {
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

function MetricCard({ label, value, detail, accent = 'neutral', footnote }) {
  return (
    <article className={`straddle-metric-card accent-${accent}`}>
      <span className="straddle-metric-label">{label}</span>
      <span className="straddle-metric-value">{value}</span>
      {detail && <span className="straddle-metric-detail">{detail}</span>}
      {footnote && <span className="straddle-metric-footnote">{footnote}</span>}
    </article>
  )
}

function ActionCard({ onRefresh, closeReferenceCount }) {
  return (
    <article className="straddle-metric-card straddle-action-card">
      <span className="straddle-metric-label">Actions</span>
      <button type="button" className="btn-refresh straddle-refresh-button" onClick={onRefresh}>
        Refresh
      </button>
      <span className="straddle-metric-footnote">
        4PM refs: {closeReferenceCount}
      </span>
    </article>
  )
}

function ChartFrame({ title, legend, children, note }) {
  return (
    <article className="straddle-chart-panel">
      <div className="straddle-chart-panel-head">
        <div>
          <h3>{title}</h3>
          {legend && <span>{legend}</span>}
        </div>
      </div>
      {children}
      {note && <div className="straddle-chart-note">{note}</div>}
    </article>
  )
}

function EmptyChart({ title, legend, message }) {
  return (
    <ChartFrame title={title} legend={legend}>
      <div className="straddle-chart-empty">{message}</div>
    </ChartFrame>
  )
}

function TermStructureChart({ rows = [] }) {
  const validRows = rows
    .filter((row) => Number.isFinite(Number(row?.iv)) && Number.isFinite(Number(row?.implied_move_pct)))
    .slice(0, 10)

  if (!validRows.length) {
    return (
      <EmptyChart
        title="Vol Term Structure"
        legend="IV and implied move by DTE"
        message="Waiting for enough rows to draw the term structure."
      />
    )
  }

  const ivValues = validRows.map((row) => Number(row.iv) * 100)
  const moveValues = validRows.map((row) => Number(row.implied_move_pct) * 100)
  const ivMin = Math.max(0, Math.floor(Math.min(...ivValues) - 1))
  const ivMax = Math.ceil(Math.max(...ivValues) + 1)
  const moveMin = Math.max(0, Math.min(...moveValues) * 0.6)
  const moveMax = Math.max(...moveValues) * 1.1
  const width = 420
  const height = 220
  const leftPad = 42
  const rightPad = 34
  const topPad = 18
  const bottomPad = 30
  const innerWidth = width - leftPad - rightPad
  const innerHeight = height - topPad - bottomPad
  const step = innerWidth / Math.max(validRows.length, 1)
  const barWidth = Math.min(22, step * 0.5)
  const gridLevels = 4

  const linePoints = validRows
    .map((row, index) => {
      const x = leftPad + step * index + step / 2
      const y = topPad + ((moveMax - Number(row.implied_move_pct) * 100) / Math.max(moveMax - moveMin, 0.0001)) * innerHeight
      return `${x},${y}`
    })
    .join(' ')

  return (
    <ChartFrame title="Vol Term Structure (0-10 DTE)" legend="IV bars + implied move line">
      <svg className="straddle-chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Vol term structure">
        {Array.from({ length: gridLevels + 1 }).map((_, index) => {
          const y = topPad + (index / gridLevels) * innerHeight
          return (
            <line
              key={index}
              x1={leftPad}
              y1={y}
              x2={width - rightPad}
              y2={y}
              className="straddle-chart-grid-line"
            />
          )
        })}
        {validRows.map((row, index) => {
          const iv = Number(row.iv) * 100
          const x = leftPad + step * index + (step - barWidth) / 2
          const barHeight = ((iv - ivMin) / Math.max(ivMax - ivMin, 0.0001)) * innerHeight
          const y = topPad + innerHeight - barHeight
          return (
            <g key={`${row.expiration}-${row.days_to_expiry}`}>
              <rect x={x} y={y} width={barWidth} height={barHeight} className="straddle-term-bar" rx="3" />
              <text x={x + barWidth / 2} y={height - 10} className="straddle-chart-label">{row.days_to_expiry}</text>
            </g>
          )
        })}
        <polyline className="straddle-term-line" points={linePoints} />
        {validRows.map((row, index) => {
          const x = leftPad + step * index + step / 2
          const y = topPad + ((moveMax - Number(row.implied_move_pct) * 100) / Math.max(moveMax - moveMin, 0.0001)) * innerHeight
          return <circle key={`dot-${row.expiration}`} cx={x} cy={y} r="3.2" className="straddle-term-dot" />
        })}
        <text x={10} y={topPad + 8} className="straddle-chart-scale">{ivMax.toFixed(0)}%</text>
        <text x={10} y={height - bottomPad + 2} className="straddle-chart-scale">{ivMin.toFixed(0)}%</text>
        <text x={width - rightPad + 4} y={topPad + 8} className="straddle-chart-scale is-right">{moveMax.toFixed(1)}%</text>
        <text x={width - rightPad + 4} y={height - bottomPad + 2} className="straddle-chart-scale is-right">{moveMin.toFixed(1)}%</text>
      </svg>
    </ChartFrame>
  )
}

function LineChart({ title, points = [], tone = 'warm', legend }) {
  const validPoints = points.filter((point) => Number.isFinite(Number(point?.value)))
  if (!validPoints.length) {
    return (
      <EmptyChart
        title={title}
        legend={legend}
        message="History will populate at 1-minute intervals during market hours."
      />
    )
  }

  const values = validPoints.map((point) => Number(point.value))
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const chartWidth = 420
  const chartHeight = 220
  const paddingX = 40
  const paddingTop = 18
  const paddingBottom = 30
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
    <ChartFrame title={title} legend={legend} note={`Last ${formatPrice(lastPoint.value)} at ${formatTimeLabel(lastPoint.timestamp)}`}>
      <svg className="straddle-chart-svg" viewBox={`0 0 ${chartWidth} ${chartHeight}`} role="img" aria-label={title}>
        {Array.from({ length: 6 }).map((_, index) => {
          const x = paddingX + (index / 5) * innerWidth
          return (
            <line
              key={`v-${index}`}
              x1={x}
              y1={paddingTop}
              x2={x}
              y2={chartHeight - paddingBottom}
              className="straddle-chart-grid-line"
            />
          )
        })}
        {Array.from({ length: 5 }).map((_, index) => {
          const y = paddingTop + (index / 4) * innerHeight
          return (
            <line
              key={`h-${index}`}
              x1={paddingX}
              y1={y}
              x2={chartWidth - paddingX}
              y2={y}
              className="straddle-chart-grid-line"
            />
          )
        })}
        <polyline className={`straddle-chart-line tone-${tone}`} points={polyline} />
        {validPoints.map((point, index) => {
          const x = paddingX + (index / Math.max(validPoints.length - 1, 1)) * innerWidth
          const y = paddingTop + ((max - Number(point.value)) / range) * innerHeight
          return <circle key={`${point.timestamp}-${index}`} className={`straddle-chart-dot tone-${tone}`} cx={x} cy={y} r="2.4" />
        })}
        <text x={10} y={paddingTop + 8} className="straddle-chart-scale">{formatPrice(max)}</text>
        <text x={10} y={chartHeight - paddingBottom + 2} className="straddle-chart-scale">{formatPrice(min)}</text>
      </svg>
    </ChartFrame>
  )
}

function QuoteCell({ bid, ask }) {
  return (
    <span className="straddle-quote-cell">
      <span>{formatPrice(bid)}</span>
      <span className="straddle-quote-sep">/</span>
      <span>{formatPrice(ask)}</span>
    </span>
  )
}

function buildCloseReferenceRows(rows = []) {
  const grouped = new Map()
  rows.forEach((row) => {
    if (!row?.session_date) return
    const items = grouped.get(row.session_date) || []
    items.push(row)
    grouped.set(row.session_date, items)
  })

  return Array.from(grouped.entries()).map(([sessionDate, items]) => {
    const sorted = [...items].sort((a, b) => {
      const aDte = Number.isFinite(Number(a?.days_to_expiry)) ? Number(a.days_to_expiry) : 999
      const bDte = Number.isFinite(Number(b?.days_to_expiry)) ? Number(b.days_to_expiry) : 999
      if (aDte !== bDte) return aDte - bDte
      return String(a?.expiration || '').localeCompare(String(b?.expiration || ''))
    })
    const lead = sorted[0]
    return {
      sessionDate,
      capturedAt: lead?.captured_at,
      frontExpiration: lead?.expiration,
      frontStrike: lead?.strike,
      frontStraddle: lead?.straddle_mid,
      rowCount: items.length,
    }
  })
}

function CloseReferenceStrip({ rows = [] }) {
  if (!rows.length) {
    return (
      <section className="main-section straddle-close-strip">
        <div className="section-head">
          <span className="section-title">Recent 4PM Cash Closes</span>
          <span className="meta">No close snapshots yet.</span>
        </div>
      </section>
    )
  }

  return (
    <section className="main-section straddle-close-strip">
      <div className="section-head">
        <span className="section-title">Recent 4PM Cash Closes</span>
        <span className="meta">Front close reference for recent sessions.</span>
      </div>
      <div className="straddle-close-chip-row">
        {rows.map((row) => (
          <article key={row.sessionDate} className="straddle-close-chip">
            <span>{formatSessionDate(row.sessionDate)}</span>
            <strong>{formatPrice(row.frontStraddle)}</strong>
            <em>{row.frontExpiration || '--'} / {formatPrice(row.frontStrike, 0)}</em>
            <small>{formatTimeLabel(row.capturedAt)} · {row.rowCount} rows</small>
          </article>
        ))}
      </div>
    </section>
  )
}

export default function StraddlePage({ theme = 'dark', onToggleTheme, activeSection = 'straddle' }) {
  const [snapshot, setSnapshot] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [, setTick] = useState(0)

  const fetchMonitor = useCallback(async () => {
    setError(null)
    try {
      const response = await fetch(`${API_STRADDLE_MONITOR}?rows=${MONITOR_ROWS}`)
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
  const dailyCloses = snapshot?.daily_closes || []
  const closeReferenceRows = buildCloseReferenceRows(dailyCloses)
  const updatedAt = snapshot?.updated_at
  const updatedAgo = updatedAt ? Math.max(0, Math.floor((Date.now() - new Date(updatedAt).getTime()) / 1000)) : null
  const frontRow = rows[0] || null

  if (loading && !snapshot) {
    return (
      <div className="straddle-dashboard">
        <div className="straddle-toolbar">
          <AppNav activeSection={activeSection} />
          <button
            type="button"
            className="theme-toggle"
            aria-pressed={theme === 'dark'}
            onClick={onToggleTheme}
            title="Toggle theme"
          >
            Theme: {theme === 'dark' ? 'Dark' : 'Light'}
          </button>
        </div>
        <section className="main-section straddle-loading-state">
          <div className="straddle-loading-copy">
            <h1>SPX Straddle Monitor</h1>
            <p>Loading live monitor data...</p>
          </div>
        </section>
      </div>
    )
  }

  return (
    <div className="straddle-dashboard">
      <div className="straddle-toolbar">
        <AppNav activeSection={activeSection} />
        <button
          type="button"
          className="theme-toggle"
          aria-pressed={theme === 'dark'}
          onClick={onToggleTheme}
          title="Toggle theme"
        >
          Theme: {theme === 'dark' ? 'Dark' : 'Light'}
        </button>
      </div>

      <header className="straddle-title-block">
        <h1>
          <span>SPX</span> STRADDLE MONITOR
        </h1>
      </header>

      {error && (
        <section className="main-section">
          <div className="straddle-error-banner">{error}</div>
        </section>
      )}

      <section className="straddle-metric-grid">
        <MetricCard
          label="SPX Spot"
          value={formatPrice(snapshot?.spot)}
          detail={`${formatSignedPrice(snapshot?.spot_change)} (${formatSignedPct(snapshot?.spot_change_pct)})`}
          footnote={`Live SPX${snapshot?.quote_timestamp ? ` · Updated ${formatTimestamp(snapshot.quote_timestamp)}` : ''}`}
          accent="positive"
        />
        <MetricCard
          label="Active Strike"
          value={formatPrice(snapshot?.active_strike, 0)}
          detail={frontRow?.expiration || '--'}
          accent="cool"
        />
        <MetricCard
          label="VIX"
          value={formatPrice(snapshot?.vix)}
          detail={`${formatSignedPrice(snapshot?.vix_change)} (${formatSignedPct(snapshot?.vix_change_pct)})`}
          footnote="Cash-close fear gauge"
          accent="warning"
        />
        <MetricCard
          label="Status"
          value={status === 'live' ? 'Live' : status === 'stale' ? 'Stale' : 'Error'}
          detail={`Updated: ${formatTimestamp(snapshot?.chain_timestamp || snapshot?.updated_at)}`}
          accent={status === 'live' ? 'warning' : status === 'stale' ? 'neutral' : 'negative'}
        />
        <ActionCard onRefresh={fetchMonitor} closeReferenceCount={closeReferenceRows.length} />
      </section>

      <section className="main-section straddle-table-panel">
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
                const rowClass = row.days_to_expiry === 0 ? 'is-front' : row.days_to_expiry === 1 ? 'is-near' : ''
                return (
                  <tr key={row.expiration} className={rowClass}>
                    <td className={`straddle-dte-cell${row.days_to_expiry === 0 ? ' is-zero' : ''}`}>{row.days_to_expiry ?? '--'}</td>
                    <td>{formatExpiry(row.expiration)}</td>
                    <td>{formatPrice(row.strike, 0)}</td>
                    <td><QuoteCell bid={row.call_bid} ask={row.call_ask} /></td>
                    <td><QuoteCell bid={row.put_bid} ask={row.put_ask} /></td>
                    <td className="straddle-table-strong is-straddle">{formatPrice(row.straddle_mid)}</td>
                    <td>{formatPrice(row.implied_move_points, 1)}</td>
                    <td className="is-warning">{formatRatio(row.put_call_skew)}</td>
                    <td className="is-warning">{formatVolPct(row.iv)}</td>
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
        <TermStructureChart rows={rows} />
        <LineChart title="Intraday Straddle (0DTE)" legend="Front expiry minute monitor" points={history['0dte'] || []} tone="warm" />
        <LineChart title="Intraday Straddle (1DTE)" legend="Next expiry minute monitor" points={history['1dte'] || []} tone="cool" />
      </section>

      <CloseReferenceStrip rows={closeReferenceRows} />

      <section className="straddle-footer-meta">
        <span>Rows: {rows.length}</span>
        <span>Quote age: {updatedAgo == null ? '--' : `${updatedAgo}s`}</span>
        <span>Close capture: {snapshot?.daily_close_capture_time || '16:00 ET'}</span>
      </section>
    </div>
  )
}
