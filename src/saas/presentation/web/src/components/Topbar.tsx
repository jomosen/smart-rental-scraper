import { fmtAgo } from '../lib/format'

interface Props {
  dataUpdatedAt: string | null
  email: string
  onLogout: () => void
  onToggleSidebar: () => void
}

export default function Topbar({ dataUpdatedAt, email, onLogout, onToggleSidebar }: Props) {
  const initial = (email || '?').charAt(0).toUpperCase()
  return (
    <div className="topbar">
      <button className="tb-burger" aria-label="Colapsar barra lateral" onClick={onToggleSidebar}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
        </svg>
      </button>
      <div className="tspacer" />
      <div className="databadge">
        <span className="dot-ok" /> Datos actualizados {fmtAgo(dataUpdatedAt)}
      </div>
      <div className="tb-bell">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0" />
        </svg>
      </div>
      <div className="tb-user">
        <div className="sb-avatar">{initial}</div>
        <div>
          <div className="uemail">{email}</div>
          <div className="logout" onClick={onLogout}>Salir</div>
        </div>
      </div>
    </div>
  )
}
