import type { ReactNode } from 'react'
import type { Meta } from '../types'
import { fmtDate } from '../lib/format'

interface Props {
  meta: Meta
  onPrev: () => void
  onNext: () => void
  action?: ReactNode
  onOpenSettings?: () => void
  loading?: boolean
}

export default function ZoneNav({ meta, onPrev, onNext, action, onOpenSettings, loading }: Props) {
  const { zone, providers, master_rep_date } = meta
  const masterName = providers.find((p) => p.is_master)?.name ?? '—'
  const providerCount = providers.length
  return (
    <div className="zone-sticky">
      <div className="zonenav">
        <button className="arrow" aria-label="Zona anterior" disabled={loading || zone.index <= 0} onClick={onPrev}>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M15 6l-6 6 6 6" />
          </svg>
        </button>
        <button className="arrow" aria-label="Zona siguiente" disabled={loading || zone.index >= zone.total - 1} onClick={onNext}>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 6l6 6-6 6" />
          </svg>
        </button>
        <div className="center">
          <div className="range">{fmtDate(zone.date_from)} – {fmtDate(zone.date_to)}</div>
          <div className="zone">temporada de {masterName} · {zone.index + 1} / {zone.total}</div>
        </div>
        {action}
      </div>
      <div className="ctxline">
        Precios obtenidos cruzando {providerCount} proveedores sobre el calendario de {masterName}
        {onOpenSettings && (
          <> <span className="ctx-link" onClick={onOpenSettings}>cambiar</span></>
        )}
        {' '}· fecha representativa {fmtDate(master_rep_date)} · precio total en € (€/día derivado)
      </div>
    </div>
  )
}
