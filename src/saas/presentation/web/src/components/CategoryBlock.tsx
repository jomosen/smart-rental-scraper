import type { Category } from '../types'
import { eur, eurDay, marketRange } from '../lib/format'
import ProviderRows from './ProviderRows'

interface Props {
  category: Category
  durations: number[]
  colorByKey: Record<string, string>
  open: boolean
  muted: boolean
  onToggle: (code: string) => void
  onToggleMute: (code: string) => void
  onCellClick: (code: string, duration: number) => void
}

export default function CategoryBlock({
  category, durations, colorByKey, open, muted, onToggle, onToggleMute, onCellClick,
}: Props) {
  const { flags } = category
  const cellByDur = new Map(category.cells.map((c) => [c.duration, c]))
  const dim = muted ? ' cat-muted' : ''

  return (
    <>
      {/* Category band: name + per-column duration headers */}
      <tr className={`cat-head${open ? ' open' : ''}${dim}`} onClick={() => onToggle(category.acriss_code)}>
        <td className="cat">
          <svg className="chev2" width="11" height="11" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {category.view_label}
          <button
            type="button"
            className="mute-btn"
            aria-pressed={muted}
            title={muted ? 'Activar categoría (se incluye en el export)' : 'Silenciar categoría (se excluye del export)'}
            onClick={(e) => { e.stopPropagation(); onToggleMute(category.acriss_code) }}
          >
            {muted ? (
              // Eye closed — silenced.
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                <line x1="1" y1="1" x2="23" y2="23" />
              </svg>
            ) : (
              // Eye open — active.
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            )}
          </button>
        </td>
        {durations.map((d) => <td className="dur" key={d}>{d}d</td>)}
      </tr>

      {/* Single summary row: badges + examples on the left, total · €/d · range per cell */}
      <tr className={`rec-row${dim}`}>
        <td className="cat">
          <div className="rowbadges">
            <span className="acode">{category.acriss_code}</span>
            {flags.partial_coverage && <span className="b cov" title="cobertura parcial" />}
            {flags.clamped && <span className="b clamp" title="ajustado por suelo/techo" />}
            {flags.stale && <span className="b stale" title="dato antiguo" />}
            {flags.anomaly && <span className="b anom" title="anomalía descartada" />}
            {flags.inferred && <span className="b inf" title="derivado de zona" />}
          </div>
          {category.catalog_examples && <div className="catexamples">{category.catalog_examples}</div>}
        </td>
        {durations.map((d) => {
          const c = cellByDur.get(d)
          if (!c || c.empty) {
            return <td className="cell empty" key={d}><div className="rec">—</div></td>
          }
          return (
            <td className="cell" key={d} onClick={() => onCellClick(category.acriss_code, d)}>
              <div className="recline"><span className="rec">{eur(c.recommended_total)}</span></div>
              <div className="sub">{eurDay(c.recommended_per_day)} · {marketRange(c.market_min, c.market_max)}</div>
            </td>
          )
        })}
      </tr>

      {open && <ProviderRows category={category} durations={durations} colorByKey={colorByKey} muted={muted} />}
    </>
  )
}
