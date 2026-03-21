import { useState, useEffect, useCallback, useRef } from 'react'
import StraddlePage from './StraddlePage'

const API_SNAPSHOT = '/api/snapshot'
const BAR_MAX_PX = 95
const AUTO_REFRESH_MS = 10_000
const QUOTE_LIVE_THRESHOLD_MS = 25_000
const TOP_VOLUME_N = 5
const TOP_OI_N = 5
const SECONDARY_HIGHLIGHT_N = 5
const MARK_LAST_OPTIONS = [0, 1, 5, 9, 15]
const THEME_STORAGE_KEY = 'dashboardTheme'
const ATR_VISIBILITY_STORAGE_KEY = 'showAtrTargets'
const SYMBOL_OPTIONS = ['SPX', 'QQQ', 'SPY', 'NDX', 'NVDA', 'TSLA', 'AAPL', 'MSFT', 'GOOGL', 'META', 'AMZN', 'IBIT', 'AVGO']
const EXPIRY_OPTIONS = [
  { key: 'slot-0dte', label: '0dte', slot: '0dte', expKey: 'slot_0dte' },
  { key: 'slot-next1', label: 'next1', slot: 'next1', expKey: 'slot_next1' },
  { key: 'slot-next2', label: 'next2', slot: 'next2', expKey: 'slot_next2' },
]
const EXPIRY_SLOT_LABELS = { '0dte': '0dte', 'next1': 'next1', 'next2': 'next2' }
const MONTH_ABBRS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTH_ABBRS_UPPER = MONTH_ABBRS.map((m) => m.toUpperCase())

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

function formatVolPct(n) {
  if (n == null) return '--'
  return `${(Number(n) * 100).toFixed(1)}%`
}

function formatSignedVolPct(n) {
  if (n == null) return '--'
  const value = Number(n) * 100
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

function formatRatio(n) {
  if (n == null) return '--'
  return Number(n).toFixed(3)
}

function formatDelta(n) {
  if (n == null) return '--'
  const value = Number(n)
  return `${value > 0 ? '+' : ''}${value.toFixed(3)}`
}

function formatSlope(n) {
  if (n == null) return '--'
  const value = Number(n)
  return `${value > 0 ? '+' : ''}${value.toFixed(4)}`
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

function getSpreadRomPct(spread) {
  const credit = Number(spread?.mark_credit)
  const width = Number(spread?.width)
  if (!Number.isFinite(credit) || !Number.isFinite(width) || width <= 0) return null
  return (credit / width) * 100
}

function getBwbRomPct(spread) {
  const rom = Number(spread?.rom_pct)
  if (Number.isFinite(rom)) return rom
  const credit = Number(spread?.mark_credit)
  const maxLoss = Number(spread?.max_loss)
  if (!Number.isFinite(credit) || !Number.isFinite(maxLoss) || maxLoss <= 0) return null
  return (credit / maxLoss) * 100
}

function getSpreadDeterministicKey(spread) {
  const fields = [
    spread?.short_strike,
    spread?.long_strike,
    spread?.low_strike,
    spread?.mid_strike,
    spread?.high_strike,
  ]
  return fields
    .map((v) => {
      const n = Number(v)
      return Number.isFinite(n) ? n.toFixed(3) : '~'
    })
    .join('|')
}

function compareSpreadsByDistanceAsc(a, b) {
  const aDist = Number(a?.distance_from_spx)
  const bDist = Number(b?.distance_from_spx)
  const aHasDist = Number.isFinite(aDist)
  const bHasDist = Number.isFinite(bDist)
  if (aHasDist !== bHasDist) return aHasDist ? -1 : 1
  if (aHasDist && aDist !== bDist) return aDist - bDist

  const aRom = Number(a?.rom_pct)
  const bRom = Number(b?.rom_pct)
  const aHasRom = Number.isFinite(aRom)
  const bHasRom = Number.isFinite(bRom)
  if (aHasRom !== bHasRom) return aHasRom ? -1 : 1
  if (aHasRom && aRom !== bRom) return bRom - aRom

  const aMark = Number(a?.mark_credit)
  const bMark = Number(b?.mark_credit)
  const aHasMark = Number.isFinite(aMark)
  const bHasMark = Number.isFinite(bMark)
  if (aHasMark !== bHasMark) return aHasMark ? -1 : 1
  if (aHasMark && aMark !== bMark) return bMark - aMark

  return getSpreadDeterministicKey(a).localeCompare(getSpreadDeterministicKey(b))
}

function formatTosExpiry(isoDate) {
  if (typeof isoDate !== 'string') return null
  const parts = isoDate.split('-')
  if (parts.length !== 3) return null
  const y = Number(parts[0])
  const m = Number(parts[1])
  const d = Number(parts[2])
  if (!Number.isInteger(y) || !Number.isInteger(m) || !Number.isInteger(d) || m < 1 || m > 12 || d < 1 || d > 31) {
    return null
  }
  const yy = String(y).slice(-2).padStart(2, '0')
  return `${String(d).padStart(2, '0')} ${MONTH_ABBRS[m - 1]} ${yy}`
}

function formatTosExpiryCompact(isoDate) {
  if (typeof isoDate !== 'string') return null
  const parts = isoDate.split('-')
  if (parts.length !== 3) return null
  const y = Number(parts[0])
  const m = Number(parts[1])
  const d = Number(parts[2])
  if (!Number.isInteger(y) || !Number.isInteger(m) || !Number.isInteger(d) || m < 1 || m > 12 || d < 1 || d > 31) {
    return null
  }
  const yy = String(y).slice(-2).padStart(2, '0')
  return `${d} ${MONTH_ABBRS_UPPER[m - 1]} ${yy}`
}

function formatTosStrike(strike) {
  const n = Number(strike)
  if (!Number.isFinite(n)) return null
  return String(Math.trunc(n))
}

function buildTosVerticalOrder({ spread, side, symbol, expiration }) {
  const symbolToken = typeof symbol === 'string' ? symbol.trim().toUpperCase() : ''
  const expiryToken = formatTosExpiry(expiration)
  const shortStrike = formatTosStrike(spread?.short_strike)
  const longStrike = formatTosStrike(spread?.long_strike)
  const mark = Number(spread?.mark_credit)
  if (!symbolToken || !expiryToken || !shortStrike || !longStrike || !Number.isFinite(mark)) return null
  const sideToken = side === 'call' ? 'CALL' : side === 'put' ? 'PUT' : null
  if (!sideToken) return null
  return `SELL -1 Vertical ${symbolToken} 100 ${expiryToken} ${shortStrike}/${longStrike} ${sideToken} @${mark.toFixed(2)} LMT`
}

function buildTosBwbOrder({ spread, side, symbol, expiration }) {
  const symbolToken = typeof symbol === 'string' ? symbol.trim().toUpperCase() : ''
  const expiryToken = formatTosExpiryCompact(expiration)
  const lowStrike = formatTosStrike(spread?.low_strike)
  const midStrike = formatTosStrike(spread?.mid_strike)
  const highStrike = formatTosStrike(spread?.high_strike)
  const mark = Number(spread?.mark_credit)
  if (!symbolToken || !expiryToken || !lowStrike || !midStrike || !highStrike || !Number.isFinite(mark)) return null
  const sideToken = side === 'call' ? 'CALL' : side === 'put' ? 'PUT' : null
  if (!sideToken) return null
  const signedCredit = (-Math.abs(mark)).toFixed(2).replace(/^-0(?=\.)/, '-')
  const strikesToken = side === 'put'
    ? `${highStrike}/${midStrike}/${lowStrike}`
    : `${lowStrike}/${midStrike}/${highStrike}`
  return `BUY +1 BUTTERFLY ${symbolToken} 100 ${expiryToken} ${strikesToken} ${sideToken} @${signedCredit} LMT`
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

function getInitialTheme() {
  if (typeof window === 'undefined') return 'dark'
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY)
  return saved === 'light' || saved === 'dark' ? saved : 'dark'
}

function getInitialStoredBoolean(storageKey, defaultValue = false) {
  if (typeof window === 'undefined') return defaultValue
  const saved = window.localStorage.getItem(storageKey)
  if (saved === 'true') return true
  if (saved === 'false') return false
  return defaultValue
}

function isStraddlePath(pathname) {
  if (typeof pathname !== 'string') return false
  const normalized = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  return normalized === '/straddle'
}

export default function App() {
  if (typeof window !== 'undefined' && isStraddlePath(window.location.pathname)) {
    return <StraddlePage />
  }
  return <OptionsDashboard />
}

function OptionsDashboard() {
  const [snapshot, setSnapshot] = useState(null)
  const [selectedSymbol, setSelectedSymbol] = useState('SPX')
  const [selectedExpirySlot, setSelectedExpirySlot] = useState('0dte')
  const [theme, setTheme] = useState(getInitialTheme)
  const [showAtr, setShowAtr] = useState(() => getInitialStoredBoolean(ATR_VISIBILITY_STORAGE_KEY, false))
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastSuccessAt, setLastSuccessAt] = useState(null)
  const [, setTick] = useState(0)
  const [markLastMin, setMarkLastMin] = useState(0)
  const [showDelta, setShowDelta] = useState(false)
  const [spreadMinRomPct, setSpreadMinRomPct] = useState(3)
  const [spreadMaxRomPct, setSpreadMaxRomPct] = useState(7)
  const [strikeDepth, setStrikeDepth] = useState(25)
  const [showBidAsk, setShowBidAsk] = useState(false)
  const [showDerivedCols, setShowDerivedCols] = useState(false)
  const [showSkew, setShowSkew] = useState(false)
  const [copiedTosKey, setCopiedTosKey] = useState(null)
  const [tosCopyError, setTosCopyError] = useState(null)
  const [tosCopyErrorSection, setTosCopyErrorSection] = useState(null)
  const [tosPreviewOrder, setTosPreviewOrder] = useState(null)
  const [tosPreviewSection, setTosPreviewSection] = useState(null)
  const copyResetRef = useRef(null)

  const fetchSnapshot = useCallback(async () => {
    setError(null)
    const query = new URLSearchParams()
    query.set('symbol', selectedSymbol)
    query.set('expiry_slot', selectedExpirySlot)
    query.set('strike_depth', String(strikeDepth))
    if (showDelta && markLastMin > 0) {
      query.set('mark_last_min', String(markLastMin))
    }
    if (showAtr) {
      query.set('include_atr', '1')
    }
    if (showSkew) {
      query.set('include_skew', '1')
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
  }, [markLastMin, selectedExpirySlot, selectedSymbol, showAtr, showDelta, showSkew, strikeDepth])

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

  useEffect(() => {
    return () => {
      if (copyResetRef.current) {
        clearTimeout(copyResetRef.current)
      }
    }
  }, [])

  const copyTosOrder = useCallback(async (orderText, rowKey, section = 'vertical') => {
    if (!orderText) return
    setTosPreviewOrder(orderText)
    setTosPreviewSection(section)
    setTosCopyError(null)
    setTosCopyErrorSection(null)
    try {
      if (typeof navigator === 'undefined' || !navigator.clipboard || !navigator.clipboard.writeText) {
        throw new Error('Clipboard unavailable')
      }
      await navigator.clipboard.writeText(orderText)
      setCopiedTosKey(rowKey)
      if (copyResetRef.current) {
        clearTimeout(copyResetRef.current)
      }
      copyResetRef.current = setTimeout(() => {
        setCopiedTosKey(null)
      }, 1200)
    } catch {
      setTosCopyError('Copy failed (clipboard blocked).')
      setTosCopyErrorSection(section)
    }
  }, [])

  const titleSymbol = snapshot?.symbol || selectedSymbol
  const titlePrice = snapshot?.symbol_price ?? snapshot?.spx_price

  useEffect(() => {
    if (typeof document === 'undefined') return
    document.title = buildDocumentTitle(titleSymbol, titlePrice, loading, error)
  }, [titleSymbol, titlePrice, loading, error])

  useEffect(() => {
    if (typeof document === 'undefined') return
    document.documentElement.dataset.theme = theme
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme)
    }
  }, [theme])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(ATR_VISIBILITY_STORAGE_KEY, String(showAtr))
  }, [showAtr])

  if (loading && !snapshot) {
    return (
      <div className="header">
        <div className="header-row">
          <span className="title">options-chain</span>
          <span className="meta">{selectedSymbol} {selectedExpirySlot}</span>
          <span className="meta">Loading…</span>
        </div>
      </div>
    )
  }

  if (error && !snapshot) {
    return (
      <div className="header">
        <div className="header-row">
          <span className="title">options-chain</span>
          <span className="meta">{selectedSymbol} {selectedExpirySlot}</span>
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
    days_to_expiry,
    expiry_slot_requested,
    expiry_slot_resolved,
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
    hot_strikes_call = [],
    hot_strikes_put = [],
    spread_scanner = {},
    atr_analysis = {},
    atr_target_spreads = {},
    skew_analysis = null,
    strikes = [],
  } = snapshot || {}
  const activeSymbol = symbol || selectedSymbol
  const activePrice = symbol_price ?? spx_price
  const requestedExpirySlot = expiry_slot_requested || selectedExpirySlot
  const resolvedExpirySlot = expiry_slot_resolved || requestedExpirySlot
  const activeExpiryLabel = EXPIRY_SLOT_LABELS[resolvedExpirySlot] || resolvedExpirySlot || '--'
  const hasExpiryFallback = requestedExpirySlot !== resolvedExpirySlot
  const activeStrikeDepth = strike_window_size ?? strikeDepth
  const callCreditSpreads = spread_scanner.call_credit_spreads || []
  const putCreditSpreads = spread_scanner.put_credit_spreads || []
  const callBwbCreditSpreads = spread_scanner.call_bwb_credit_spreads || []
  const putBwbCreditSpreads = spread_scanner.put_bwb_credit_spreads || []
  const loRomPct = Math.min(spreadMinRomPct, spreadMaxRomPct)
  const hiRomPct = Math.max(spreadMinRomPct, spreadMaxRomPct)
  const filteredCallCreditSpreads = callCreditSpreads
    .map((s) => ({ ...s, rom_pct: getSpreadRomPct(s) }))
    .filter((s) => s.rom_pct != null && s.rom_pct >= loRomPct && s.rom_pct <= hiRomPct)
    .sort(compareSpreadsByDistanceAsc)
  const filteredPutCreditSpreads = putCreditSpreads
    .map((s) => ({ ...s, rom_pct: getSpreadRomPct(s) }))
    .filter((s) => s.rom_pct != null && s.rom_pct >= loRomPct && s.rom_pct <= hiRomPct)
    .sort(compareSpreadsByDistanceAsc)
  const filteredCallBwbCreditSpreads = callBwbCreditSpreads
    .map((s) => ({ ...s, rom_pct: getBwbRomPct(s) }))
    .filter((s) => s.rom_pct != null && s.rom_pct >= loRomPct && s.rom_pct <= hiRomPct)
    .sort(compareSpreadsByDistanceAsc)
  const filteredPutBwbCreditSpreads = putBwbCreditSpreads
    .map((s) => ({ ...s, rom_pct: getBwbRomPct(s) }))
    .filter((s) => s.rom_pct != null && s.rom_pct >= loRomPct && s.rom_pct <= hiRomPct)
    .sort(compareSpreadsByDistanceAsc)
  const atrAnalysis = atr_analysis || {}
  const atrTargetSpreads = atr_target_spreads || {}
  const withAtrRom = (spread) => (spread ? { ...spread, rom_pct: getSpreadRomPct(spread) } : null)
  const atrCallRows = [
    {
      key: 'atr-call-target-1',
      band: '+1 ATR',
      side: 'call',
      spread: withAtrRom(atrTargetSpreads.call_plus_1atr),
      targetLevel: atrAnalysis.plus_1atr_level,
    },
    {
      key: 'atr-call-target-2',
      band: '+2 ATR',
      side: 'call',
      spread: withAtrRom(atrTargetSpreads.call_plus_2atr),
      targetLevel: atrAnalysis.plus_2atr_level,
    },
  ]
  const atrPutRows = [
    {
      key: 'atr-put-target-1',
      band: '-1 ATR',
      side: 'put',
      spread: withAtrRom(atrTargetSpreads.put_minus_1atr),
      targetLevel: atrAnalysis.minus_1atr_level,
    },
    {
      key: 'atr-put-target-2',
      band: '-2 ATR',
      side: 'put',
      spread: withAtrRom(atrTargetSpreads.put_minus_2atr),
      targetLevel: atrAnalysis.minus_2atr_level,
    },
  ]
  const skewMetrics = skew_analysis?.metrics || {}
  const skewNodes = skew_analysis?.nodes || {}
  const skewDiagnostics = skew_analysis?.diagnostics || {}
  const skewStatus = skew_analysis?.status || 'unavailable'
  const skewWarnings = skewDiagnostics.warnings || []
  const skewNodeRows = [
    { key: 'put_10d', label: '10Δ Put' },
    { key: 'put_25d', label: '25Δ Put' },
    { key: 'atm_50d', label: 'ATM 50Δ' },
    { key: 'call_25d', label: '25Δ Call' },
    { key: 'call_10d', label: '10Δ Call' },
  ]
  const rrClass = skewMetrics.rr_25 == null ? 'skew-value-neutral' : skewMetrics.rr_25 < 0 ? 'skew-value-warm' : skewMetrics.rr_25 > 0 ? 'skew-value-cool' : 'skew-value-neutral'
  const ratioClass = skewMetrics.put_call_iv_ratio_25 == null ? 'skew-value-neutral' : skewMetrics.put_call_iv_ratio_25 > 1 ? 'skew-value-warm' : skewMetrics.put_call_iv_ratio_25 < 1 ? 'skew-value-cool' : 'skew-value-neutral'
  const asymClass = skewMetrics.slope_asymmetry == null ? 'skew-value-neutral' : skewMetrics.slope_asymmetry < 0 ? 'skew-value-warm' : skewMetrics.slope_asymmetry > 0 ? 'skew-value-cool' : 'skew-value-neutral'

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
          <span className="title">options-chain</span>
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
              const isActive = resolvedExpirySlot === opt.slot
              const isRequested = requestedExpirySlot === opt.slot && requestedExpirySlot !== resolvedExpirySlot
              const expDate = expirations[opt.expKey]
              const isAvailable = Boolean(expDate)
              return (
                <button
                  key={opt.key}
                  type="button"
                  className={`dte-btn expiry-btn ${isActive ? 'is-active' : ''} ${isRequested ? 'is-requested' : ''}`}
                  onClick={() => setSelectedExpirySlot(opt.slot)}
                  disabled={isActive || !isAvailable}
                  title={!isAvailable ? `${opt.label} unavailable` : ''}
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
            <label>
              <input
                type="checkbox"
                checked={showAtr}
                onChange={(e) => setShowAtr(e.target.checked)}
              />
              <span>Show ATR</span>
            </label>
            <label>
              <input
                type="checkbox"
                checked={showSkew}
                onChange={(e) => setShowSkew(e.target.checked)}
              />
              <span>Show skew</span>
            </label>
            <button
              type="button"
              className="theme-toggle"
              aria-pressed={theme === 'dark'}
              onClick={() => setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))}
              title="Toggle theme"
            >
              Theme: {theme === 'dark' ? 'Dark' : 'Light'}
            </button>
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
            <span>0dte {expirations.slot_0dte || '--'}</span>
            <span>next1 {expirations.slot_next1 || '--'}</span>
            <span>next2 {expirations.slot_next2 || '--'}</span>
            <span>quote refresh ~{quote_refresh_seconds || 10}s</span>
            <span>chain refresh ~{chain_refresh_seconds || 60}s</span>
            <span>strikes ±{activeStrikeDepth}</span>
            {quoteUpdatedAgo != null && <span>quote age {quoteUpdatedAgo}s</span>}
            {chainUpdatedAgo != null && <span>chain age {chainUpdatedAgo}s</span>}
          </span>
          {hasExpiryFallback && (
            <span className="meta expiry-fallback">Requested {requestedExpirySlot} {'->'} showing {resolvedExpirySlot}</span>
          )}
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

      {showAtr && (
        <section className="main-section">
          <div className="section-head">
            <span className="section-title">ATR(14) targets (prev close anchor)</span>
            {tosCopyError && tosCopyErrorSection === 'atr' && <span className="tos-copy-error">{tosCopyError}</span>}
            {tosPreviewOrder && tosPreviewSection === 'atr' && (
              <div className="tos-preview">
                <span className="tos-preview-label">TOS order:</span> {tosPreviewOrder}
              </div>
            )}
          </div>
          <div className="atr-main">
            <span className="atr-value">ATR14: {formatPrice(atrAnalysis.atr14)}</span>
          </div>
          <div className="atr-meta">
            <span>Status: {String(atrAnalysis.status || 'unavailable').toUpperCase()}</span>
            <span>Prev Close: {formatPrice(atrAnalysis.previous_close)}</span>
            <span>+1 ATR: {formatPrice(atrAnalysis.plus_1atr_level)}</span>
            <span>-1 ATR: {formatPrice(atrAnalysis.minus_1atr_level)}</span>
            <span>+2 ATR: {formatPrice(atrAnalysis.plus_2atr_level)}</span>
            <span>-2 ATR: {formatPrice(atrAnalysis.minus_2atr_level)}</span>
            <span>As of: {atrAnalysis.asof_session || '--'}</span>
          </div>
          {atrAnalysis.status !== 'ok' && (
            <div className="atr-note">
              ATR unavailable{atrAnalysis.message ? `: ${atrAnalysis.message}` : '.'}
            </div>
          )}
          <div className="split-panels">
            <div>
              <div className="sub-title">Call @ +1/+2 ATR</div>
              <table className="mini-table">
                <thead>
                  <tr>
                    <th>Band</th>
                    <th>Short/Long</th>
                    <th>Mark</th>
                    <th>ROM%</th>
                    <th>POP (Δ)</th>
                    <th>Dist</th>
                    <th>Target</th>
                    <th>Gap</th>
                    <th className="tos-action-col">TOS</th>
                  </tr>
                </thead>
                <tbody>
                  {atrCallRows.map((row) => {
                    const spread = row.spread
                    const tosOrder = spread
                      ? buildTosVerticalOrder({ spread, side: row.side, symbol: activeSymbol, expiration })
                      : null
                    return (
                      <tr key={row.key}>
                        <td>{row.band}</td>
                        <td>{spread ? `${formatPrice(spread.short_strike)}/${formatPrice(spread.long_strike)}` : '--'}</td>
                        <td>{formatPrice(spread?.mark_credit)}</td>
                        <td>{formatPct(spread?.rom_pct)}</td>
                        <td>{formatPct(spread?.pop_delta_pct)}</td>
                        <td>{formatPrice(spread?.distance_from_spx)}</td>
                        <td>{formatPrice(row.targetLevel)}</td>
                        <td>{formatPrice(spread?.atr_gap)}</td>
                        <td className="tos-action-col">
                          <button
                            type="button"
                            className={`btn-copy-tos${copiedTosKey === row.key ? ' copied' : ''}`}
                            disabled={!tosOrder}
                            onClick={() => copyTosOrder(tosOrder, row.key, 'atr')}
                          >
                            {copiedTosKey === row.key ? 'Copied' : 'Copy'}
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <div>
              <div className="sub-title">Put @ -1/-2 ATR</div>
              <table className="mini-table">
                <thead>
                  <tr>
                    <th>Band</th>
                    <th>Short/Long</th>
                    <th>Mark</th>
                    <th>ROM%</th>
                    <th>POP (Δ)</th>
                    <th>Dist</th>
                    <th>Target</th>
                    <th>Gap</th>
                    <th className="tos-action-col">TOS</th>
                  </tr>
                </thead>
                <tbody>
                  {atrPutRows.map((row) => {
                    const spread = row.spread
                    const tosOrder = spread
                      ? buildTosVerticalOrder({ spread, side: row.side, symbol: activeSymbol, expiration })
                      : null
                    return (
                      <tr key={row.key}>
                        <td>{row.band}</td>
                        <td>{spread ? `${formatPrice(spread.short_strike)}/${formatPrice(spread.long_strike)}` : '--'}</td>
                        <td>{formatPrice(spread?.mark_credit)}</td>
                        <td>{formatPct(spread?.rom_pct)}</td>
                        <td>{formatPct(spread?.pop_delta_pct)}</td>
                        <td>{formatPrice(spread?.distance_from_spx)}</td>
                        <td>{formatPrice(row.targetLevel)}</td>
                        <td>{formatPrice(spread?.atr_gap)}</td>
                        <td className="tos-action-col">
                          <button
                            type="button"
                            className={`btn-copy-tos${copiedTosKey === row.key ? ' copied' : ''}`}
                            disabled={!tosOrder}
                            onClick={() => copyTosOrder(tosOrder, row.key, 'atr')}
                          >
                            {copiedTosKey === row.key ? 'Copied' : 'Copy'}
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

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

      {showSkew && (
        <section className="main-section skew-section">
          <div className="section-head">
            <span className="section-title">Skew Deep Dive</span>
            <span className={`skew-status ${skewStatus}`}>{String(skewStatus).toUpperCase()}</span>
            <span className="meta">{skew_analysis?.method || 'delta_iv_nodes'}</span>
          </div>
          <div className="skew-metrics-grid">
            <div className="skew-metric-card">
              <div className="skew-metric-label">ATM IV</div>
              <div className="skew-metric-value skew-value-neutral">{formatVolPct(skewMetrics.atm_iv)}</div>
            </div>
            <div className="skew-metric-card">
              <div className="skew-metric-label">25Δ RR</div>
              <div className={`skew-metric-value ${rrClass}`}>{formatSignedVolPct(skewMetrics.rr_25)}</div>
            </div>
            <div className="skew-metric-card">
              <div className="skew-metric-label">25Δ BF</div>
              <div className="skew-metric-value skew-value-neutral">{formatSignedVolPct(skewMetrics.bf_25)}</div>
            </div>
            <div className="skew-metric-card">
              <div className="skew-metric-label">25Δ Put/Call IV Ratio</div>
              <div className={`skew-metric-value ${ratioClass}`}>{formatRatio(skewMetrics.put_call_iv_ratio_25)}</div>
            </div>
            <div className="skew-metric-card">
              <div className="skew-metric-label">Wing Slope Asymmetry</div>
              <div className={`skew-metric-value ${asymClass}`}>{formatSlope(skewMetrics.slope_asymmetry)}</div>
            </div>
          </div>
          <div className="skew-help-box">
            <div className="skew-help-title">How to read these (beginner)</div>
            <div className="skew-help-row">
              <span className="skew-help-label">ATM IV</span>
              <span>Market’s baseline volatility near current price. Higher = bigger expected moves.</span>
            </div>
            <div className="skew-help-row">
              <span className="skew-help-label">25Δ RR</span>
              <span>Call IV minus Put IV at 25Δ. Negative means puts are pricier (more downside fear).</span>
            </div>
            <div className="skew-help-row">
              <span className="skew-help-label">25Δ BF</span>
              <span>Wing average IV vs ATM IV. Higher means wings are richer than ATM; near 0 means similar.</span>
            </div>
            <div className="skew-help-row">
              <span className="skew-help-label">25Δ Put/Call IV Ratio</span>
              <span>Put IV divided by Call IV at 25Δ. Above 1 = puts richer; below 1 = calls richer.</span>
            </div>
            <div className="skew-help-row">
              <span className="skew-help-label">Wing Slope Asymmetry</span>
              <span>How unbalanced downside vs upside wing steepness is. Bigger absolute value = stronger imbalance.</span>
            </div>
            <div className="skew-help-tip">
              Tip: Use RR + Put/Call Ratio first for direction; use BF/Asymmetry as context.
            </div>
          </div>
          <div className="split-panels">
            <div>
              <div className="sub-title">Delta/IV Nodes</div>
              <table className="mini-table">
                <thead>
                  <tr>
                    <th>Node</th>
                    <th>Strike</th>
                    <th>Delta</th>
                    <th>IV</th>
                  </tr>
                </thead>
                <tbody>
                  {skewNodeRows.map((entry) => {
                    const node = skewNodes[entry.key] || {}
                    return (
                      <tr key={entry.key}>
                        <td>{entry.label}</td>
                        <td>{formatPrice(node.strike)}</td>
                        <td>{formatDelta(node.delta)}</td>
                        <td>{formatVolPct(node.iv)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <div>
              <div className="sub-title">Diagnostics</div>
              <div className="skew-diagnostics">
                <div>symbol {skew_analysis?.symbol || activeSymbol} / exp {skew_analysis?.expiration || expiration || '--'} / spot {formatPrice(skew_analysis?.spot ?? activePrice)}</div>
                <div>dte {skew_analysis?.days_to_expiry ?? days_to_expiry ?? '--'} / coverage {formatPct(skewDiagnostics.greeks_coverage_pct)}</div>
                <div>available {Array.isArray(skewDiagnostics.available_nodes) && skewDiagnostics.available_nodes.length ? skewDiagnostics.available_nodes.join(', ') : '--'}</div>
                <div>missing {Array.isArray(skewDiagnostics.missing_nodes) && skewDiagnostics.missing_nodes.length ? skewDiagnostics.missing_nodes.join(', ') : '--'}</div>
                <div className="skew-warning">
                  warnings {skewWarnings.length ? skewWarnings.join(' | ') : '--'}
                </div>
              </div>
            </div>
          </div>
        </section>
      )}

      <section className="main-section">
        <div className="section-head">
          <span className="section-title">Far OTM vertical spreads (adjacent strike)</span>
          <div className="section-controls spread-filters">
            <label>
              ROM min %:
              <input
                type="number"
                min="0"
                max="100"
                step="0.1"
                value={spreadMinRomPct}
                onChange={(e) => setSpreadMinRomPct(Number(e.target.value || 0))}
              />
            </label>
            <label>
              ROM max %:
              <input
                type="number"
                min="0"
                max="100"
                step="0.1"
                value={spreadMaxRomPct}
                onChange={(e) => setSpreadMaxRomPct(Number(e.target.value || 0))}
              />
            </label>
            <button type="button" className="btn-refresh" onClick={() => { setSpreadMinRomPct(3); setSpreadMaxRomPct(7) }}>
              3-7%
            </button>
          </div>
          {tosCopyError && tosCopyErrorSection === 'vertical' && <span className="tos-copy-error">{tosCopyError}</span>}
          {tosPreviewOrder && tosPreviewSection === 'vertical' && (
            <div className="tos-preview">
              <span className="tos-preview-label">TOS order:</span> {tosPreviewOrder}
            </div>
          )}
        </div>
        <div className="split-panels">
          <div>
            <div className="sub-title">Call credit spreads</div>
            <table className="mini-table">
              <thead>
                <tr>
                  <th>Short/Long</th>
                  <th>Mark</th>
                  <th>ROM%</th>
                  <th>Bid</th>
                  <th>Ask</th>
                  <th>POP (Δ)</th>
                  <th>Dist</th>
                  <th className="tos-action-col">TOS</th>
                </tr>
              </thead>
              <tbody>
                {filteredCallCreditSpreads.length === 0 ? (
                  <tr><td colSpan="8" className="empty-cell">No call spreads in ROM range {formatPct(loRomPct)}-{formatPct(hiRomPct)}</td></tr>
                ) : filteredCallCreditSpreads.map((s) => {
                  const rowKey = `cs-${s.short_strike}-${s.long_strike}`
                  const tosOrder = buildTosVerticalOrder({ spread: s, side: 'call', symbol: activeSymbol, expiration })
                  return (
                    <tr key={rowKey}>
                      <td>{formatPrice(s.short_strike)}/{formatPrice(s.long_strike)}</td>
                      <td>{formatPrice(s.mark_credit)}</td>
                      <td>{formatPct(s.rom_pct)}</td>
                      <td>{formatPrice(s.bid_credit)}</td>
                      <td>{formatPrice(s.ask_credit)}</td>
                      <td>{formatPct(s.pop_delta_pct)}</td>
                      <td>{formatPrice(s.distance_from_spx)}</td>
                      <td className="tos-action-col">
                        <button
                          type="button"
                          className={`btn-copy-tos${copiedTosKey === rowKey ? ' copied' : ''}`}
                          disabled={!tosOrder}
                          onClick={() => copyTosOrder(tosOrder, rowKey, 'vertical')}
                        >
                          {copiedTosKey === rowKey ? 'Copied' : 'Copy'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
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
                  <th>ROM%</th>
                  <th>Bid</th>
                  <th>Ask</th>
                  <th>POP (Δ)</th>
                  <th>Dist</th>
                  <th className="tos-action-col">TOS</th>
                </tr>
              </thead>
              <tbody>
                {filteredPutCreditSpreads.length === 0 ? (
                  <tr><td colSpan="8" className="empty-cell">No put spreads in ROM range {formatPct(loRomPct)}-{formatPct(hiRomPct)}</td></tr>
                ) : filteredPutCreditSpreads.map((s) => {
                  const rowKey = `ps-${s.short_strike}-${s.long_strike}`
                  const tosOrder = buildTosVerticalOrder({ spread: s, side: 'put', symbol: activeSymbol, expiration })
                  return (
                    <tr key={rowKey}>
                      <td>{formatPrice(s.short_strike)}/{formatPrice(s.long_strike)}</td>
                      <td>{formatPrice(s.mark_credit)}</td>
                      <td>{formatPct(s.rom_pct)}</td>
                      <td>{formatPrice(s.bid_credit)}</td>
                      <td>{formatPrice(s.ask_credit)}</td>
                      <td>{formatPct(s.pop_delta_pct)}</td>
                      <td>{formatPrice(s.distance_from_spx)}</td>
                      <td className="tos-action-col">
                        <button
                          type="button"
                          className={`btn-copy-tos${copiedTosKey === rowKey ? ' copied' : ''}`}
                          disabled={!tosOrder}
                          onClick={() => copyTosOrder(tosOrder, rowKey, 'vertical')}
                        >
                          {copiedTosKey === rowKey ? 'Copied' : 'Copy'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="main-section">
        <div className="section-head">
          <span className="section-title">Far OTM broken-wing butterflies (credit)</span>
          {tosCopyError && tosCopyErrorSection === 'bwb' && <span className="tos-copy-error">{tosCopyError}</span>}
          {tosPreviewOrder && tosPreviewSection === 'bwb' && (
            <div className="tos-preview">
              <span className="tos-preview-label">TOS order:</span> {tosPreviewOrder}
            </div>
          )}
        </div>
        <div className="split-panels">
          <div>
            <div className="sub-title">Call BWB credit spreads</div>
            <table className="mini-table">
              <thead>
                <tr>
                  <th>U/M/L</th>
                  <th>Mark</th>
                  <th>ROM%</th>
                  <th>Max Loss</th>
                  <th>Max Profit</th>
                  <th>BE</th>
                  <th>POP (Δ)</th>
                  <th>Dist</th>
                  <th className="tos-action-col">TOS</th>
                </tr>
              </thead>
              <tbody>
                {filteredCallBwbCreditSpreads.length === 0 ? (
                  <tr><td colSpan="9" className="empty-cell">No call BWB spreads in ROM range {formatPct(loRomPct)}-{formatPct(hiRomPct)}</td></tr>
                ) : filteredCallBwbCreditSpreads.map((s) => {
                  const rowKey = `bwb-c-${s.low_strike}-${s.mid_strike}-${s.high_strike}`
                  const tosOrder = buildTosBwbOrder({ spread: s, side: 'call', symbol: activeSymbol, expiration })
                  return (
                    <tr key={rowKey}>
                      <td>{formatPrice(s.low_strike)}/{formatPrice(s.mid_strike)}/{formatPrice(s.high_strike)}</td>
                      <td>{formatPrice(s.mark_credit)}</td>
                      <td>{formatPct(s.rom_pct)}</td>
                      <td>{formatPrice(s.max_loss)}</td>
                      <td>{formatPrice(s.max_profit)}</td>
                      <td>{formatPrice(s.breakeven)}</td>
                      <td>{formatPct(s.pop_delta_pct)}</td>
                      <td>{formatPrice(s.distance_from_spx)}</td>
                      <td className="tos-action-col">
                        <button
                          type="button"
                          className={`btn-copy-tos${copiedTosKey === rowKey ? ' copied' : ''}`}
                          disabled={!tosOrder}
                          onClick={() => copyTosOrder(tosOrder, rowKey, 'bwb')}
                        >
                          {copiedTosKey === rowKey ? 'Copied' : 'Copy'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div>
            <div className="sub-title">Put BWB credit spreads</div>
            <table className="mini-table">
              <thead>
                <tr>
                  <th>L/M/U</th>
                  <th>Mark</th>
                  <th>ROM%</th>
                  <th>Max Loss</th>
                  <th>Max Profit</th>
                  <th>BE</th>
                  <th>POP (Δ)</th>
                  <th>Dist</th>
                  <th className="tos-action-col">TOS</th>
                </tr>
              </thead>
              <tbody>
                {filteredPutBwbCreditSpreads.length === 0 ? (
                  <tr><td colSpan="9" className="empty-cell">No put BWB spreads in ROM range {formatPct(loRomPct)}-{formatPct(hiRomPct)}</td></tr>
                ) : filteredPutBwbCreditSpreads.map((s) => {
                  const rowKey = `bwb-p-${s.low_strike}-${s.mid_strike}-${s.high_strike}`
                  const tosOrder = buildTosBwbOrder({ spread: s, side: 'put', symbol: activeSymbol, expiration })
                  return (
                    <tr key={rowKey}>
                      <td>{formatPrice(s.high_strike)}/{formatPrice(s.mid_strike)}/{formatPrice(s.low_strike)}</td>
                      <td>{formatPrice(s.mark_credit)}</td>
                      <td>{formatPct(s.rom_pct)}</td>
                      <td>{formatPrice(s.max_loss)}</td>
                      <td>{formatPrice(s.max_profit)}</td>
                      <td>{formatPrice(s.breakeven)}</td>
                      <td>{formatPct(s.pop_delta_pct)}</td>
                      <td>{formatPrice(s.distance_from_spx)}</td>
                      <td className="tos-action-col">
                        <button
                          type="button"
                          className={`btn-copy-tos${copiedTosKey === rowKey ? ' copied' : ''}`}
                          disabled={!tosOrder}
                          onClick={() => copyTosOrder(tosOrder, rowKey, 'bwb')}
                        >
                          {copiedTosKey === rowKey ? 'Copied' : 'Copy'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </>
  )
}
