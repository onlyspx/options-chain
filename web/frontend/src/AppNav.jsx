const NAV_ITEMS = [
  { href: '/', key: 'dashboard', label: 'Dashboard' },
  { href: '/straddle', key: 'straddle', label: 'Straddle' },
]

export function normalizeAppPath(pathname) {
  if (typeof pathname !== 'string' || !pathname) return '/'
  const normalized = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  return normalized || '/'
}

export function isStraddlePath(pathname) {
  return normalizeAppPath(pathname) === '/straddle'
}

export function getActiveAppSection(pathname) {
  return isStraddlePath(pathname) ? 'straddle' : 'dashboard'
}

export default function AppNav({ activeSection = 'dashboard' }) {
  return (
    <nav className="app-nav" aria-label="Primary">
      {NAV_ITEMS.map((item) => {
        const isActive = item.key === activeSection
        return (
          <a
            key={item.key}
            href={item.href}
            className={`app-nav-link${isActive ? ' is-active' : ''}`}
            aria-current={isActive ? 'page' : undefined}
          >
            {item.label}
          </a>
        )
      })}
    </nav>
  )
}
