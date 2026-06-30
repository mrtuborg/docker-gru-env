import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

/**
 * /pipelines redirects automatically:
 * - To the first pipeline's Blueprint if any exist
 * - To /pipelines/new if none exist
 */
export default function Pipelines() {
  const navigate = useNavigate()

  useEffect(() => {
    fetch('/api/pipelines')
      .then(r => r.json())
      .then((list: { id: string }[]) => {
        if (list.length > 0) navigate(`/pipelines/${list[0].id}`, { replace: true })
        else navigate('/pipelines/new', { replace: true })
      })
      .catch(() => navigate('/pipelines/new', { replace: true }))
  }, [])

  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'center', color: 'var(--muted)', padding: 48 }}>
      <div className="spinner"/> Loading…
    </div>
  )
}
