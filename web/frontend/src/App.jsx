import { useState, useEffect, useCallback } from 'react'

const API_SNAPSHOT = '/api/snapshot'
const BAR_MAX_PX = 95
const AUTO_REFRESH_MS = 60_000
const LIVE_THRESHOLD_MS = 90_000
const TOP_VOLUME_N = 5
const MARK_LAST_OPTIONS = [0, 1, 5, 9, 15]

function formatTimestamp(iso) {
  if (!iso) return '--'
  try {
    const d = new Date(iso)
    const date = d.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' }).replace(/\//g, '.')
    const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true })
    return `${date} ${time}`
  } catch {
    return iso
  }
}

function formatPrice(n) {
  if (n == null) return '--'
  return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatInt(n) {
  if (n == null) return '0'
  return Number(n).toLocaleString()
}

function getConnectionStatus(snapshot, lastSuccessAt, error, loading) {
  if (error && !snapshot) return 'error'
  if (typeof navigator !== 'undefined' && !navigator.onLine) return 'error'
  if (!snapshot || lastSuccessAt == null) return null
  const age = Date.now() - lastSuccessAt
  return age <= LIVE_THRESHOLD_MS ? 'live' : 'stale'
}

export default function App() {
  const [snapshot, setSnapshot] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastSuccessAt, setLastSuccessAt] = useState(null)
  const [, setTick] = useState(0)
  const [markLastMin, setMarkLastMin] = useState(0)
  const [showDelta, setShowDelta] = useState(false)

  const fetchSnapshot = useCallback(async () => {
    setError(null)
    const url =
      showDelta && markLastMin > 0
        ? `${API_SNAPSHOT}?mark_last_min=${markLastMin}`
        : API_SNAPSHOT
    try {
      const res = await fetch(url)
      if (!res.ok) {
        const t = await res.text()
        throw new Error(t || `HTTP ${res.status}`)
      }
      const data = await res.json()
      setSnapshot(data)
      setLastSuccessAt(Date.now())
    } catch (e) {
      setError(e.message || 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [markLastMin, showDelta])

  useEffect(() => {
    fetchSnapshot()
  }, [fetchSnapshot])

  useEffect(() => {
    if (!snapshot) return
    const id = setInterval(fetchSnapshot, AUTO_REFRESH_MS)
    return () => clearInterval(id)
  }, [snapshot, fetchSnapshot])

  useEffect(() => {
    if (!snapshot) return
    const id = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [snapshot])

  if (loading && !snapshot) {
    return (
      <div className="header">
        <div className="header-row">
          <span className="title">SPX 0DTE Dashboard</span>
          <span className="meta">Loading…</span>
        </div>
      </div>
    )
  }

  if (error && !snapshot) {
    return (
      <div className="header">
        <div className="header-row">
          <span className="title">SPX 0DTE Dashboard</span>
          <span className="status-pill error">Offline</span>
          <span className="error-msg">{error}</span>
          <button type="button" className="btn-refresh" onClick={fetchSnapshot}>Refresh</button>
        </div>
      </div>
    )
  }

  const { expiration, spx_price, timestamp, strikes = [] } = snapshot || {}
  const connectionStatus = getConnectionStatus(snapshot, lastSuccessAt, error, loading)
  const updatedAgo =
    lastSuccessAt != null
      ? Math.max(0, Math.floor((Date.now() - lastSuccessAt) / 1000))
      : null
  const tsDisplay = formatTimestamp(timestamp)
  const maxVol = strikes.length
    ? Math.max(
        ...strikes.flatMap((s) => [
          s.put_vol ?? 0,
          s.call_vol ?? 0,
        ])
      )
    : 1
  const scale = (v) => (v != null && maxVol > 0 ? Math.round((Number(v) / maxVol) * BAR_MAX_PX) : 0)

  const atmStrike =
    spx_price != null && strikes.length
      ? strikes.reduce((best, s) =>
          Math.abs((s.strike ?? 0) - spx_price) < Math.abs((best.strike ?? 0) - spx_price) ? s : best
        )
      : null

  const highPutVolumeStrikes = new Set(
    [...strikes]
      .sort((a, b) => (b.put_vol ?? 0) - (a.put_vol ?? 0))
      .slice(0, TOP_VOLUME_N)
      .map((s) => s.strike)
  )
  const highCallVolumeStrikes = new Set(
    [...strikes]
      .sort((a, b) => (b.call_vol ?? 0) - (a.call_vol ?? 0))
      .slice(0, TOP_VOLUME_N)
      .map((s) => s.strike)
  )

  return (
    <>
      <header className="header">
        <div className="header-row">
          <span className="title">SPX 0DTE Dashboard</span>
          {connectionStatus && (
            <>
              <span className={`status-pill ${connectionStatus}`}>
                {connectionStatus === 'live' ? 'LIVE' : connectionStatus === 'stale' ? 'STALE' : 'Offline'}
              </span>
              {updatedAgo != null && connectionStatus !== 'error' && (
                <span className="meta">Updated {updatedAgo}s ago</span>
              )}
            </>
          )}
          <span className="spx-label">SPX</span>
          <span className="spx-price">{formatPrice(spx_price)} $</span>
          <span className="meta">last price {tsDisplay}</span>
          <span className="meta">timestamp: {tsDisplay}</span>
          <span className="metrics">
            <span>dte0</span>
          </span>
          <button type="button" className="btn-refresh" onClick={fetchSnapshot}>
            Refresh
          </button>
        </div>
        <div className="toggles">
          <label><input type="checkbox" defaultChecked readOnly /><span>volume</span></label>
          <label><input type="checkbox" /><span>open interest</span></label>
        </div>
      </header>

      <section className="main-section">
        <div className="section-head">
          <span className="section-title">volume {tsDisplay}</span>
          <div className="section-controls">
            <span>
              Mark last:{' '}
              <select
                value={markLastMin}
                onChange={(e) => setMarkLastMin(Number(e.target.value))}
              >
                {MARK_LAST_OPTIONS.map((m) => (
                  <option key={m} value={m}>
                    {m} min
                  </option>
                ))}
              </select>
            </span>
            <label>
              <input
                type="checkbox"
                checked={showDelta}
                onChange={(e) => setShowDelta(e.target.checked)}
              />{' '}
              Show delta:
            </label>
            <label><input type="checkbox" /> Show netto:</label>
          </div>
        </div>

        <table className="strike-table">
          <thead>
            <tr>
              <th style={{ textAlign: 'right' }}>Δ put</th>
              <th style={{ textAlign: 'right' }}>put</th>
              <th className="bar-cell" />
              <th className="strike-col">strike</th>
              <th className="bar-cell" />
              <th style={{ textAlign: 'left' }}>call</th>
              <th>Δ call</th>
              <th style={{ textAlign: 'right' }}>netto</th>
              <th style={{ textAlign: 'right' }}>Σ</th>
              <th style={{ textAlign: 'right' }}>PCR</th>
            </tr>
          </thead>
          <tbody>
            {strikes.map((row) => {
              const putVol = row.put_vol ?? 0
              const callVol = row.call_vol ?? 0
              const netto = callVol - putVol
              const sum = callVol + putVol
              const pcr = callVol > 0 ? (putVol / callVol).toFixed(2) : (putVol > 0 ? '—' : '0')
              const isAtm = atmStrike && row.strike === atmStrike.strike
              const deltaPut = row.delta_put
              const deltaCall = row.delta_call
              const isHighPutVol = highPutVolumeStrikes.has(row.strike)
              const isHighCallVol = highCallVolumeStrikes.has(row.strike)
              return (
                <tr key={row.strike} className={isAtm ? 'atm' : ''}>
                  <td
                    style={{ textAlign: 'right' }}
                    className={
                      deltaPut != null
                        ? deltaPut > 0
                          ? 'netto pos'
                          : deltaPut < 0
                            ? 'netto neg'
                            : ''
                        : ''
                    }
                  >
                    {deltaPut != null ? (deltaPut > 0 ? '+' : '') + formatInt(deltaPut) : '—'}
                  </td>
                  <td className={`put-num ${isHighPutVol ? 'high-put-volume' : ''}`}>{formatInt(row.put_vol)}</td>
                  <td className={`bar-cell ${isHighPutVol ? 'high-put-volume' : ''}`}>
                    <div className="bar-wrap put">
                      <div className="bar put" style={{ width: scale(row.put_vol) + 'px' }} />
                    </div>
                  </td>
                  <td className="strike-col">-{row.strike}-</td>
                  <td className={`bar-cell ${isHighCallVol ? 'high-call-volume' : ''}`}>
                    <div className="bar-wrap call">
                      <div className="bar call" style={{ width: scale(row.call_vol) + 'px' }} />
                    </div>
                  </td>
                  <td className={`call-num ${isHighCallVol ? 'high-call-volume' : ''}`}>{formatInt(row.call_vol)}</td>
                  <td
                    className={
                      deltaCall != null
                        ? deltaCall > 0
                          ? 'netto pos'
                          : deltaCall < 0
                            ? 'netto neg'
                            : ''
                        : ''
                    }
                  >
                    {deltaCall != null ? (deltaCall > 0 ? '+' : '') + formatInt(deltaCall) : '—'}
                  </td>
                  <td className={`netto ${netto < 0 ? 'neg' : netto > 0 ? 'pos' : ''}`}>
                    {netto > 0 ? '+' : ''}{formatInt(netto)}
                  </td>
                  <td className="sum-col">{formatInt(sum)}</td>
                  <td style={{ textAlign: 'right' }}>{pcr}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>
    </>
  )
}
