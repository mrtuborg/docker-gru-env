type Status = 'healthy' | 'degraded' | 'error' | 'unknown'

const STATUS_MAP: Record<Status, { cls: string; dot: string; label: string }> = {
  healthy:  { cls: 'badge-healthy',  dot: 'dot-green',  label: 'Healthy' },
  degraded: { cls: 'badge-degraded', dot: 'dot-yellow', label: 'Degraded' },
  error:    { cls: 'badge-error',    dot: 'dot-red',    label: 'Error' },
  unknown:  { cls: 'badge-unknown',  dot: 'dot-muted',  label: 'Unknown' },
}

export default function HealthBadge({ status, message }: { status?: string; message?: string }) {
  const s = (status as Status) || 'unknown'
  const m = STATUS_MAP[s] || STATUS_MAP.unknown
  return (
    <span className={`badge ${m.cls}`} title={message || ''} style={{ display:'inline-flex', alignItems:'center', gap:5 }}>
      <span className={`dot ${m.dot}`} style={{ width:6, height:6 }}/>
      {m.label}
      {message && <span style={{ opacity:.7 }}>— {message.slice(0,40)}{message.length > 40 ? '…' : ''}</span>}
    </span>
  )
}
