import React from 'react'

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

  const inner = (
    <>
      <span className={`dot ${m.dot}`} style={{ width: 6, height: 6, flexShrink: 0 }}/>
      <span>{needsAuth ? 'Re-authorize' : m.label}</span>
    </>
  )

  const sharedStyle: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 5,
  }

  if (needsAuth && onAuth) {
    return (
      <button
        className={`badge ${m.cls}`}
        title={message || 'Click to re-authorize'}
        onClick={onAuth}
        style={{ ...sharedStyle, cursor: 'pointer', border: 'none' }}
      >
        {inner}
      </button>
    )
  }

  return (
    <span className={`badge ${m.cls}`} title={message || ''} style={sharedStyle}>
      {inner}
    </span>
  )
}
