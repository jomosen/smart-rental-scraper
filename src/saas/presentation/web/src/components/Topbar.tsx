import { fmtAgo } from '../lib/format'

interface Props {
  providerCount: number
  dataUpdatedAt: string | null
  email: string
  onLogout: () => void
}

export default function Topbar({ providerCount, dataUpdatedAt, email, onLogout }: Props) {
  const initial = (email || '?').charAt(0).toUpperCase()
  return (
    <div className="topbar">
      <div>
        <div className="vtitle">Radar de precios</div>
        <div className="vsub">Mercado por segmento · {providerCount} proveedores en el radar</div>
      </div>
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
