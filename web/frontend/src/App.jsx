import { useState, useEffect, useCallback } from 'react'

const API_SNAPSHOT = '/api/snapshot'
const BAR_MAX_PX = 95
const AUTO_REFRESH_MS = 10_000
const QUOTE_LIVE_THRESHOLD_MS = 25_000
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

function formatSigned(n) {
  if (n == null) return '--'
  return `${n > 0 ? '+' : ''}${formatInt(n)}`
}

function getConnectionStatus(snapshot, quoteAgeMs, error, loading) {
  if (error && !snapshot) return 'error'
  if (typeof navigator !== 'undefined' && !navigator.onLine) return 'error'
  if (!snapshot || quoteAgeMs == null) return null
  return quoteAgeMs <= QUOTE_LIVE_THRESHOLD_MS ? 'live' : 'stale'
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

  const {
    expiration,
    spx_price,
    timestamp,
    quote_timestamp,
    chain_timestamp,
    expected_move,
    em_low,
    em_high,
    em_strike,
    em_call_mid,
    em_put_mid,
    quote_refresh_seconds,
    chain_refresh_seconds,
    hot_strikes_call = [],
    hot_strikes_put = [],
    spread_scanner = {},
    strikes = [],
  } = snapshot || {}
  const callCreditSpreads = spread_scanner.call_credit_spreads || []
  const putCreditSpreads = spread_scanner.put_credit_spreads || []

  const quoteUpdatedAt = quote_timestamp ? new Date(quote_timestamp).getTime() : lastSuccessAt
  const chainUpdatedAt = chain_timestamp ? new Date(chain_timestamp).getTime() : lastSuccessAt
  const quoteAgeMs = quoteUpdatedAt != null ? Math.max(0, Date.now() - quoteUpdatedAt) : null
  const chainAgeMs = chainUpdatedAt != null ? Math.max(0, Date.now() - chainUpdatedAt) : null
  const quoteUpdatedAgo = quoteAgeMs != null ? Math.floor(quoteAgeMs / 1000) : null
  const chainUpdatedAgo = chainAgeMs != null ? Math.floor(chainAgeMs / 1000) : null
  const connectionStatus = getConnectionStatus(snapshot, quoteAgeMs, error, loading)

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
              {quoteUpdatedAgo != null && connectionStatus !== 'error' && (
                <span className="meta">Quote {quoteUpdatedAgo}s ago</span>
              )}
            </>
          )}
          <span className="spx-label">SPX</span>
          <span className="spx-price">{formatPrice(spx_price)} $</span>
          <span className="meta">Quote ts: {formatTimestamp(quote_timestamp || timestamp)}</span>
          <span className="meta">Chain ts: {formatTimestamp(chain_timestamp || timestamp)}</span>
          <span className="metrics">
            <span>dte0 / exp {expiration || '--'}</span>
            <span>quote refresh ~{quote_refresh_seconds || 10}s</span>
            <span>chain refresh ~{chain_refresh_seconds || 60}s</span>
            {quoteUpdatedAgo != null && <span>quote age {quoteUpdatedAgo}s</span>}
            {chainUpdatedAgo != null && <span>chain age {chainUpdatedAgo}s</span>}
          </span>
          <button type="button" className="btn-refresh" onClick={fetchSnapshot}>
            Refresh
          </button>
        </div>
        <div className="em-card">
          <div className="em-main">
            <span className="em-label">Expected Move (to expiration)</span>
            <span className="em-value">{expected_move != null ? `±${formatPrice(expected_move)}` : '--'}</span>
          </div>
          <div className="em-meta">
            <span>Range: {em_low != null && em_high != null ? `${formatPrice(em_low)} - ${formatPrice(em_high)}` : '--'}</span>
            <span>ATM strike: {em_strike != null ? formatPrice(em_strike) : '--'}</span>
            <span>Call mid: {em_call_mid != null ? formatPrice(em_call_mid) : '--'}</span>
            <span>Put mid: {em_put_mid != null ? formatPrice(em_put_mid) : '--'}</span>
          </div>
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

      <section className="main-section">
        <div className="section-head">
          <span className="section-title">Hot strikes (5m volume delta)</span>
        </div>
        <div className="split-panels">
          <div>
            <div className="sub-title">Calls</div>
            <table className="mini-table">
              <thead>
                <tr>
                  <th>Strike</th>
                  <th>Now</th>
                  <th>5m ago</th>
                  <th>Δ 5m</th>
                </tr>
              </thead>
              <tbody>
                {hot_strikes_call.length === 0 ? (
                  <tr><td colSpan="4" className="empty-cell">No call hot strikes yet</td></tr>
                ) : hot_strikes_call.map((row) => (
                  <tr key={`hc-${row.strike}`}>
                    <td>{formatPrice(row.strike)}</td>
                    <td>{formatInt(row.current_vol)}</td>
                    <td>{formatInt(row.vol_5m_ago)}</td>
                    <td className="netto pos">{formatSigned(row.delta_5m)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <div className="sub-title">Puts</div>
            <table className="mini-table">
              <thead>
                <tr>
                  <th>Strike</th>
                  <th>Now</th>
                  <th>5m ago</th>
                  <th>Δ 5m</th>
                </tr>
              </thead>
              <tbody>
                {hot_strikes_put.length === 0 ? (
                  <tr><td colSpan="4" className="empty-cell">No put hot strikes yet</td></tr>
                ) : hot_strikes_put.map((row) => (
                  <tr key={`hp-${row.strike}`}>
                    <td>{formatPrice(row.strike)}</td>
                    <td>{formatInt(row.current_vol)}</td>
                    <td>{formatInt(row.vol_5m_ago)}</td>
                    <td className="netto pos">{formatSigned(row.delta_5m)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="main-section">
        <div className="section-head">
          <span className="section-title">Far OTM vertical spreads (5-wide, mark ≤ 0.50)</span>
        </div>
        <div className="split-panels">
          <div>
            <div className="sub-title">Call credit spreads</div>
            <table className="mini-table">
              <thead>
                <tr>
                  <th>Short/Long</th>
                  <th>Mark</th>
                  <th>Bid</th>
                  <th>Ask</th>
                  <th>Dist</th>
                </tr>
              </thead>
              <tbody>
                {callCreditSpreads.length === 0 ? (
                  <tr><td colSpan="5" className="empty-cell">No call spreads matching filter</td></tr>
                ) : callCreditSpreads.map((s) => (
                  <tr key={`cs-${s.short_strike}-${s.long_strike}`}>
                    <td>{formatPrice(s.short_strike)}/{formatPrice(s.long_strike)}</td>
                    <td>{formatPrice(s.mark_credit)}</td>
                    <td>{formatPrice(s.bid_credit)}</td>
                    <td>{formatPrice(s.ask_credit)}</td>
                    <td>{formatPrice(s.distance_from_spx)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <div className="sub-title">Put credit spreads</div>
            <table className="mini-table">
              <thead>
                <tr>
                  <th>Short/Long</th>
                  <th>Mark</th>
                  <th>Bid</th>
                  <th>Ask</th>
                  <th>Dist</th>
                </tr>
              </thead>
              <tbody>
                {putCreditSpreads.length === 0 ? (
                  <tr><td colSpan="5" className="empty-cell">No put spreads matching filter</td></tr>
                ) : putCreditSpreads.map((s) => (
                  <tr key={`ps-${s.short_strike}-${s.long_strike}`}>
                    <td>{formatPrice(s.short_strike)}/{formatPrice(s.long_strike)}</td>
                    <td>{formatPrice(s.mark_credit)}</td>
                    <td>{formatPrice(s.bid_credit)}</td>
                    <td>{formatPrice(s.ask_credit)}</td>
                    <td>{formatPrice(s.distance_from_spx)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </>
  )
}
