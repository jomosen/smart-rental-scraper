interface Props {
  tenantName: string
  plan: string | null
}

export default function Sidebar({ tenantName, plan }: Props) {
  return (
    <aside className="sidebar">
      <div className="sb-brand">
        <span className="brand-dot" />
        <div>
          <div className="tname">{tenantName || 'RentRadar'}</div>
          <div className="tplan">{plan ? `Plan ${plan}` : 'Plan demo'}</div>
        </div>
      </div>
      <nav className="sb-nav">
        <div className="sb-item active">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 3v18h18M7 14l4-4 3 3 5-6" />
          </svg>
          Radar de precios
        </div>
        <div className="sb-item soon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3 3" />
          </svg>
          Histórico <span className="tag">PRONTO</span>
        </div>
        <div className="sb-item soon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0" />
          </svg>
          Alertas <span className="tag">PRONTO</span>
        </div>
      </nav>
      <div className="sb-spacer" />
    </aside>
  )
}
