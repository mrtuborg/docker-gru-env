type Status = 'healthy' | 'degraded' | 'error' | 'unknown'

const STATUS_MAP: Record<Status, { cls: string; dot: string; label: string }> = {
  healthy:  { cls: 'badge-healthy',  dot: 'dot-green',  label: 'Healthy' },
  degraded: { cls: 'badge-degraded', dot: 'dot-yellow', label: 'Degraded' },
  error:    { cls: 'badge-error',    dot: 'dot-red',    label: 'Error' },
  unknown:  { cls: 'badge-unknown',  dot: 'dot-muted',  label: 'Checking…' },
}

interface HealthBadgeProps {
  status?: string
  message?: string
  needsAuth?: boolean
  onAuth?: () => void
}

export default function HealthBadge({ status, message, needsAuth, onAuth }: HealthBadgeProps) {
  const s = (status as Status) || 'unknown'
  const m = STATUS_MAP[s] || STATUS_MAP.unknown

  // When re-auth is needed: a full-width button showing the error message
  if (needsAuth && onAuth) {
    return (
      <button
        onClick={onAuth}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          width: '100%', padding: '8px 12px', borderRadius: 8,
          background: 'color-mix(in srgb, var(--red) 12%, transparent)',
          border: '1px solid color-mix(in srgb, var(--red) 40%, transparent)',
          color: 'var(--red)', cursor: 'pointer', textAlign: 'left',
          fontSize: 12, fontWeight: 500, lineHeight: 1.3,
        }}
      >
        <span className="dot dot-red" style={{ flexShrink: 0 }}/>
        <span style={{ flex: 1 }}>{message || 'Authentication error — click to re-authorize'}</span>
        <span style={{ opacity: 0.7, fontSize: 11, whiteSpace: 'nowrap' }}>Re-authorize →</span>
      </button>
    )
  }

  return (
    <span
      className={`badge ${m.cls}`}
      title={message || ''}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}
    >
      <span className={`dot ${m.dot}`} style={{ width: 6, height: 6, flexShrink: 0 }}/>
      <span>{m.label}</span>
    </span>
  )
}
