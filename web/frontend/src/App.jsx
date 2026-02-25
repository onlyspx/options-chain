import { useState, useEffect, useCallback } from 'react'

const API_SNAPSHOT = '/api/snapshot'
const BAR_MAX_PX = 95
const AUTO_REFRESH_MS = 10_000
const QUOTE_LIVE_THRESHOLD_MS = 25_000
const TOP_VOLUME_N = 5
const TOP_OI_N = 5
const SECONDARY_HIGHLIGHT_N = 5
const MARK_LAST_OPTIONS = [0, 1, 5, 9, 15]
const SYMBOL_OPTIONS = ['SPX', 'QQQ', 'SPY', 'NDX']
const EXPIRY_OPTIONS = [
  { key: 'dte-0', label: '0dte', mode: 'dte', dte: 0, expKey: 'dte0' },
  { key: 'dte-1', label: '1dte', mode: 'dte', dte: 1, expKey: 'dte1' },
  { key: 'friday', label: 'Fri weekly', mode: 'friday', dte: 1, expKey: 'friday' },
]

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

function formatPct(n) {
  if (n == null) return '--'
  return `${Number(n).toFixed(1)}%`
}

function formatExpiryDateShort(isoDate) {
  if (!isoDate) return '--'
  try {
    const [y, m, d] = isoDate.split('-').map(Number)
    const dt = new Date(y, (m || 1) - 1, d || 1)
    return dt.toLocaleDateString('en-US', { weekday: 'short', month: '2-digit', day: '2-digit' })
  } catch {
    return isoDate
  }
}

function getConnectionStatus(snapshot, quoteAgeMs, error) {
  if (error && !snapshot) return 'error'
  if (typeof navigator !== 'undefined' && !navigator.onLine) return 'error'
  if (!snapshot || quoteAgeMs == null) return null
  return quoteAgeMs <= QUOTE_LIVE_THRESHOLD_MS ? 'live' : 'stale'
}

function buildDocumentTitle(symbol, price, loading, error) {
  const safeSymbol = symbol || 'SPX'
  if (loading && price == null) return `${safeSymbol} --`
  if (error && price == null) return `${safeSymbol} --`
  return `${safeSymbol} ${formatPrice(price)}`
}

export default function App() {
  const [snapshot, setSnapshot] = useState(null)
  const [selectedSymbol, setSelectedSymbol] = useState('SPX')
  const [selectedDte, setSelectedDte] = useState(0)
  const [selectedExpiryMode, setSelectedExpiryMode] = useState('dte')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastSuccessAt, setLastSuccessAt] = useState(null)
  const [, setTick] = useState(0)
  const [markLastMin, setMarkLastMin] = useState(0)
  const [showDelta, setShowDelta] = useState(false)
  const [spreadMinCredit, setSpreadMinCredit] = useState(0.25)
  const [spreadMaxCredit, setSpreadMaxCredit] = useState(0.5)
  const [strikeDepth, setStrikeDepth] = useState(25)
  const [showBidAsk, setShowBidAsk] = useState(false)
  const [showDerivedCols, setShowDerivedCols] = useState(false)

  const fetchSnapshot = useCallback(async () => {
    setError(null)
    const query = new URLSearchParams()
    query.set('symbol', selectedSymbol)
    query.set('dte', String(selectedDte))
    query.set('expiry_mode', selectedExpiryMode)
    query.set('strike_depth', String(strikeDepth))
    if (showDelta && markLastMin > 0) {
      query.set('mark_last_min', String(markLastMin))
    }
    const url = `${API_SNAPSHOT}?${query.toString()}`
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
  }, [markLastMin, selectedDte, selectedExpiryMode, selectedSymbol, showDelta, strikeDepth])

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

  const titleSymbol = snapshot?.symbol || selectedSymbol
  const titlePrice = snapshot?.symbol_price ?? snapshot?.spx_price

  useEffect(() => {
    if (typeof document === 'undefined') return
    document.title = buildDocumentTitle(titleSymbol, titlePrice, loading, error)
  }, [titleSymbol, titlePrice, loading, error])

  if (loading && !snapshot) {
    return (
      <div className="header">
        <div className="header-row">
          <span className="title">Options Dashboard</span>
          <span className="meta">{selectedSymbol} {selectedExpiryMode === 'friday' ? 'friday' : `dte${selectedDte}`}</span>
          <span className="meta">Loading…</span>
        </div>
      </div>
    )
  }

  if (error && !snapshot) {
    return (
      <div className="header">
        <div className="header-row">
          <span className="title">Options Dashboard</span>
          <span className="meta">{selectedSymbol} {selectedExpiryMode === 'friday' ? 'friday' : `dte${selectedDte}`}</span>
          <span className="status-pill error">Offline</span>
          <span className="error-msg">{error}</span>
          <button type="button" className="btn-refresh" onClick={fetchSnapshot}>Refresh</button>
        </div>
      </div>
    )
  }

  const {
    symbol,
    symbol_price,
    expiration,
    expirations = {},
    expiry_mode,
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
    strike_window_size,
    dte,
    hot_strikes_call = [],
    hot_strikes_put = [],
    spread_scanner = {},
    strikes = [],
  } = snapshot || {}
  const activeSymbol = symbol || selectedSymbol
  const activePrice = symbol_price ?? spx_price
  const activeExpiryMode = expiry_mode || selectedExpiryMode
  const activeExpiryLabel = activeExpiryMode === 'friday' ? 'friday weekly' : `dte${dte ?? selectedDte}`
  const activeStrikeDepth = strike_window_size ?? strikeDepth
  const callCreditSpreads = spread_scanner.call_credit_spreads || []
  const putCreditSpreads = spread_scanner.put_credit_spreads || []
  const loCredit = Math.min(spreadMinCredit, spreadMaxCredit)
  const hiCredit = Math.max(spreadMinCredit, spreadMaxCredit)
  const filteredCallCreditSpreads = callCreditSpreads.filter((s) => {
    const mark = Number(s.mark_credit)
    return Number.isFinite(mark) && mark >= loCredit && mark <= hiCredit
  })
  const filteredPutCreditSpreads = putCreditSpreads.filter((s) => {
    const mark = Number(s.mark_credit)
    return Number.isFinite(mark) && mark >= loCredit && mark <= hiCredit
  })

  const quoteUpdatedAt = quote_timestamp ? new Date(quote_timestamp).getTime() : lastSuccessAt
  const chainUpdatedAt = chain_timestamp ? new Date(chain_timestamp).getTime() : lastSuccessAt
  const quoteAgeMs = quoteUpdatedAt != null ? Math.max(0, Date.now() - quoteUpdatedAt) : null
  const chainAgeMs = chainUpdatedAt != null ? Math.max(0, Date.now() - chainUpdatedAt) : null
  const quoteUpdatedAgo = quoteAgeMs != null ? Math.floor(quoteAgeMs / 1000) : null
  const chainUpdatedAgo = chainAgeMs != null ? Math.floor(chainAgeMs / 1000) : null
  const connectionStatus = getConnectionStatus(snapshot, quoteAgeMs, error)

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
    activePrice != null && strikes.length
      ? strikes.reduce((best, s) =>
          Math.abs((s.strike ?? 0) - activePrice) < Math.abs((best.strike ?? 0) - activePrice) ? s : best
        )
      : null

  const rankedPutVolumeStrikes = [...strikes]
    .sort((a, b) => (b.put_vol ?? 0) - (a.put_vol ?? 0))
    .map((s) => s.strike)
  const highPutVolumeStrikes = new Set(rankedPutVolumeStrikes.slice(0, TOP_VOLUME_N))
  const midPutVolumeStrikes = new Set(
    rankedPutVolumeStrikes.slice(TOP_VOLUME_N, TOP_VOLUME_N + SECONDARY_HIGHLIGHT_N)
  )

  const rankedCallVolumeStrikes = [...strikes]
    .sort((a, b) => (b.call_vol ?? 0) - (a.call_vol ?? 0))
    .map((s) => s.strike)
  const highCallVolumeStrikes = new Set(rankedCallVolumeStrikes.slice(0, TOP_VOLUME_N))
  const midCallVolumeStrikes = new Set(
    rankedCallVolumeStrikes.slice(TOP_VOLUME_N, TOP_VOLUME_N + SECONDARY_HIGHLIGHT_N)
  )

  const rankedPutOiStrikes = [...strikes]
    .filter((s) => s.put_oi != null)
    .sort((a, b) => Number(b.put_oi ?? 0) - Number(a.put_oi ?? 0))
    .map((s) => s.strike)
  const highPutOiStrikes = new Set(rankedPutOiStrikes.slice(0, TOP_OI_N))
  const midPutOiStrikes = new Set(
    rankedPutOiStrikes.slice(TOP_OI_N, TOP_OI_N + SECONDARY_HIGHLIGHT_N)
  )

  const rankedCallOiStrikes = [...strikes]
    .filter((s) => s.call_oi != null)
    .sort((a, b) => Number(b.call_oi ?? 0) - Number(a.call_oi ?? 0))
    .map((s) => s.strike)
  const highCallOiStrikes = new Set(rankedCallOiStrikes.slice(0, TOP_OI_N))
  const midCallOiStrikes = new Set(
    rankedCallOiStrikes.slice(TOP_OI_N, TOP_OI_N + SECONDARY_HIGHLIGHT_N)
  )

  return (
    <>
      <header className="header">
        <div className="header-row">
          <span className="title">Options Dashboard</span>
          <div className="symbol-picker">
            {SYMBOL_OPTIONS.map((sym) => (
              <button
                key={sym}
                type="button"
                className={`dte-btn ${selectedSymbol === sym ? 'is-active' : ''}`}
                onClick={() => setSelectedSymbol(sym)}
                disabled={selectedSymbol === sym}
              >
                {sym}
              </button>
            ))}
          </div>
          <div className="symbol-dte-grid">
            {EXPIRY_OPTIONS.map((opt) => {
              const isActive = selectedExpiryMode === opt.mode && selectedDte === opt.dte
              const expDate = expirations[opt.expKey]
              return (
                <button
                  key={opt.key}
                  type="button"
                  className={`dte-btn expiry-btn ${isActive ? 'is-active' : ''}`}
                  onClick={() => {
                    setSelectedExpiryMode(opt.mode)
                    setSelectedDte(opt.dte)
                  }}
                  disabled={isActive}
                >
                  <span>{opt.label}</span>
                  <span className="expiry-date">{formatExpiryDateShort(expDate)}</span>
                </button>
              )
            })}
          </div>
          <div className="section-controls header-controls">
            <label>
              Strikes ±
              <input
                type="number"
                min="5"
                max="100"
                step="1"
                value={strikeDepth}
                onChange={(e) => {
                  const raw = Number(e.target.value)
                  if (!Number.isFinite(raw)) {
                    setStrikeDepth(25)
                    return
                  }
                  const clamped = Math.max(5, Math.min(100, Math.trunc(raw)))
                  setStrikeDepth(clamped)
                }}
              />
            </label>
          </div>
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
          <span className="spx-label">{activeSymbol}</span>
          <span className="spx-price">{formatPrice(activePrice)} $</span>
          <span className="meta">Quote ts: {formatTimestamp(quote_timestamp || timestamp)}</span>
          <span className="meta">Chain ts: {formatTimestamp(chain_timestamp || timestamp)}</span>
          <span className="metrics">
            <span>{activeExpiryLabel} / exp {expiration || '--'}</span>
            <span>0dte {expirations.dte0 || '--'}</span>
            <span>1dte {expirations.dte1 || '--'}</span>
            <span>fri {expirations.friday || '--'}</span>
            <span>quote refresh ~{quote_refresh_seconds || 10}s</span>
            <span>chain refresh ~{chain_refresh_seconds || 60}s</span>
            <span>strikes ±{activeStrikeDepth}</span>
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
          <label><input type="checkbox" defaultChecked readOnly /><span>open interest</span></label>
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
            <label>
              <input
                type="checkbox"
                checked={showBidAsk}
                onChange={(e) => setShowBidAsk(e.target.checked)}
              />{' '}
              Show bid/ask
            </label>
            <label>
              <input
                type="checkbox"
                checked={showDerivedCols}
                onChange={(e) => setShowDerivedCols(e.target.checked)}
              />{' '}
              Show netto/Σ/PCR
            </label>
          </div>
        </div>

        <table className="strike-table">
          <thead>
            <tr>
              {showDelta && <th style={{ textAlign: 'right' }}>Δ put</th>}
              {showBidAsk && <th style={{ textAlign: 'right' }}>Put Bid</th>}
              {showBidAsk && <th style={{ textAlign: 'right' }}>Put Ask</th>}
              <th style={{ textAlign: 'right' }}>OI</th>
              <th style={{ textAlign: 'right' }}>Volume</th>
              <th className="bar-cell" />
              <th className="strike-col">strike</th>
              <th className="bar-cell" />
              <th style={{ textAlign: 'left' }}>Volume</th>
              <th style={{ textAlign: 'right' }}>OI</th>
              {showBidAsk && <th style={{ textAlign: 'right' }}>Call Bid</th>}
              {showBidAsk && <th style={{ textAlign: 'right' }}>Call Ask</th>}
              {showDelta && <th style={{ textAlign: 'right' }}>Δ call</th>}
              {showDerivedCols && <th style={{ textAlign: 'right' }}>netto</th>}
              {showDerivedCols && <th style={{ textAlign: 'right' }}>Σ</th>}
              {showDerivedCols && <th style={{ textAlign: 'right' }}>PCR</th>}
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
              const isMidPutVol = midPutVolumeStrikes.has(row.strike)
              const isMidCallVol = midCallVolumeStrikes.has(row.strike)
              const isHighPutOi = highPutOiStrikes.has(row.strike)
              const isHighCallOi = highCallOiStrikes.has(row.strike)
              const isMidPutOi = midPutOiStrikes.has(row.strike)
              const isMidCallOi = midCallOiStrikes.has(row.strike)
              const putVolClass = isHighPutVol ? 'high-put-volume' : isMidPutVol ? 'mid-put-volume' : ''
              const callVolClass = isHighCallVol ? 'high-call-volume' : isMidCallVol ? 'mid-call-volume' : ''
              const putOiClass = isHighPutOi ? 'high-put-oi' : isMidPutOi ? 'mid-put-oi' : ''
              const callOiClass = isHighCallOi ? 'high-call-oi' : isMidCallOi ? 'mid-call-oi' : ''
              return (
                <tr key={row.strike} className={isAtm ? 'atm' : ''}>
                  {showDelta && (
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
                  )}
                  {showBidAsk && <td style={{ textAlign: 'right' }}>{formatPrice(row.put_bid)}</td>}
                  {showBidAsk && <td style={{ textAlign: 'right' }}>{formatPrice(row.put_ask)}</td>}
                  <td className={`put-oi-num ${putOiClass}`}>{formatInt(row.put_oi)}</td>
                  <td className={`put-num ${putVolClass}`}>{formatInt(row.put_vol)}</td>
                  <td className={`bar-cell ${putVolClass}`}>
                    <div className="bar-wrap put">
                      <div className="bar put" style={{ width: scale(row.put_vol) + 'px' }} />
                    </div>
                  </td>
                  <td className="strike-col">-{row.strike}-</td>
                  <td className={`bar-cell ${callVolClass}`}>
                    <div className="bar-wrap call">
                      <div className="bar call" style={{ width: scale(row.call_vol) + 'px' }} />
                    </div>
                  </td>
                  <td className={`call-num ${callVolClass}`}>{formatInt(row.call_vol)}</td>
                  <td className={`call-oi-num ${callOiClass}`}>{formatInt(row.call_oi)}</td>
                  {showBidAsk && <td style={{ textAlign: 'right' }}>{formatPrice(row.call_bid)}</td>}
                  {showBidAsk && <td style={{ textAlign: 'right' }}>{formatPrice(row.call_ask)}</td>}
                  {showDelta && (
                    <td
                      style={{ textAlign: 'right' }}
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
                  )}
                  {showDerivedCols && (
                    <td className={`netto ${netto < 0 ? 'neg' : netto > 0 ? 'pos' : ''}`}>
                      {netto > 0 ? '+' : ''}{formatInt(netto)}
                    </td>
                  )}
                  {showDerivedCols && <td className="sum-col">{formatInt(sum)}</td>}
                  {showDerivedCols && <td style={{ textAlign: 'right' }}>{pcr}</td>}
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
          <span className="section-title">Far OTM vertical spreads (adjacent strike, mark &le; 0.50)</span>
          <div className="section-controls spread-filters">
            <label>
              Credit min:
              <input
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={spreadMinCredit}
                onChange={(e) => setSpreadMinCredit(Number(e.target.value || 0))}
              />
            </label>
            <label>
              Credit max:
              <input
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={spreadMaxCredit}
                onChange={(e) => setSpreadMaxCredit(Number(e.target.value || 0))}
              />
            </label>
            <button type="button" className="btn-refresh" onClick={() => { setSpreadMinCredit(0.25); setSpreadMaxCredit(0.5) }}>
              0.25-0.50
            </button>
          </div>
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
                  <th>POP</th>
                  <th>Dist</th>
                </tr>
              </thead>
              <tbody>
                {filteredCallCreditSpreads.length === 0 ? (
                  <tr><td colSpan="6" className="empty-cell">No call spreads in credit range {formatPrice(loCredit)}-{formatPrice(hiCredit)}</td></tr>
                ) : filteredCallCreditSpreads.map((s) => (
                  <tr key={`cs-${s.short_strike}-${s.long_strike}`}>
                    <td>{formatPrice(s.short_strike)}/{formatPrice(s.long_strike)}</td>
                    <td>{formatPrice(s.mark_credit)}</td>
                    <td>{formatPrice(s.bid_credit)}</td>
                    <td>{formatPrice(s.ask_credit)}</td>
                    <td>{formatPct(s.pop_pct)}</td>
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
                  <th>POP</th>
                  <th>Dist</th>
                </tr>
              </thead>
              <tbody>
                {filteredPutCreditSpreads.length === 0 ? (
                  <tr><td colSpan="6" className="empty-cell">No put spreads in credit range {formatPrice(loCredit)}-{formatPrice(hiCredit)}</td></tr>
                ) : filteredPutCreditSpreads.map((s) => (
                  <tr key={`ps-${s.short_strike}-${s.long_strike}`}>
                    <td>{formatPrice(s.short_strike)}/{formatPrice(s.long_strike)}</td>
                    <td>{formatPrice(s.mark_credit)}</td>
                    <td>{formatPrice(s.bid_credit)}</td>
                    <td>{formatPrice(s.ask_credit)}</td>
                    <td>{formatPct(s.pop_pct)}</td>
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
