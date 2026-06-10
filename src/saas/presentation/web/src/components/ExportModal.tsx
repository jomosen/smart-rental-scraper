import { useEffect, useState } from 'react'
import type { Season } from '../types'
import { zoneRangeShort } from '../lib/format'
import Modal from './Modal'

interface Props {
  open: boolean
  seasons: Season[]
  onClose: () => void
  onExport: (format: 'csv' | 'pdf', zoneFrom: number, zoneTo: number) => void
}

/** Format picker + season range for the export. Choosing a card downloads and closes. */
export default function ExportModal({ open, seasons, onClose, onExport }: Props) {
  const last = Math.max(0, seasons.length - 1)
  const [from, setFrom] = useState(0)
  const [to, setTo] = useState(last)

  // Reset to the full range each time the modal opens (or the seasons change).
  useEffect(() => {
    if (open) { setFrom(0); setTo(last) }
  }, [open, last])

  // "Temporada 1 · 2–8 jun" — dates so the user doesn't pick blind.
  const label = (s: Season) => {
    const range = zoneRangeShort(s.date_from, s.date_to)
    return `Temporada ${s.index + 1}${range ? ` · ${range}` : ''}`
  }

  // Keep the range coherent: moving one end past the other drags it along.
  const onFrom = (v: number) => { setFrom(v); if (v > to) setTo(v) }
  const onTo = (v: number) => { setTo(v); if (v < from) setFrom(v) }

  const pick = (format: 'csv' | 'pdf') => {
    onExport(format, from, to)
    onClose()
  }

  return (
    <Modal open={open} title="Exportar tarifas" onClose={onClose}>
      <div className="export-seasons">
        <label className="export-field">
          <span>Desde</span>
          <select value={from} onChange={(e) => onFrom(Number(e.target.value))}>
            {seasons.map((s) => <option key={s.index} value={s.index}>{label(s)}</option>)}
          </select>
        </label>
        <label className="export-field">
          <span>Hasta</span>
          <select value={to} onChange={(e) => onTo(Number(e.target.value))}>
            {seasons.map((s) => <option key={s.index} value={s.index}>{label(s)}</option>)}
          </select>
        </label>
      </div>
      <div className="export-options">
        <button className="export-card" onClick={() => pick('csv')}>
          <span className="export-card-title">CSV</span>
          <span className="export-card-desc">Para hoja de cálculo</span>
        </button>
        <button className="export-card" onClick={() => pick('pdf')}>
          <span className="export-card-title">PDF</span>
          <span className="export-card-desc">Informe para cliente</span>
        </button>
      </div>
    </Modal>
  )
}
