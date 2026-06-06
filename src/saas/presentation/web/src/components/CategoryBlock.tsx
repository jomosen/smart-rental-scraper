import type { Category } from '../types'
import { eur, eurDay, marketRange } from '../lib/format'
import ProviderRows from './ProviderRows'

interface Props {
  category: Category
  durations: number[]
  colorByKey: Record<string, string>
  open: boolean
  onToggle: (code: string) => void
  onCellClick: (code: string, duration: number) => void
}

export default function CategoryBlock({
  category, durations, colorByKey, open, onToggle, onCellClick,
}: Props) {
  const { flags } = category
  const cellByDur = new Map(category.cells.map((c) => [c.duration, c]))

  return (
    <>
      {/* Category band: name + per-column duration headers */}
      <tr className={`cat-head${open ? ' open' : ''}`} onClick={() => onToggle(category.acriss_code)}>
        <td className="cat">
          <svg className="chev2" width="11" height="11" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {category.view_label}
        </td>
        {durations.map((d) => <td className="dur" key={d}>{d}d</td>)}
      </tr>

      {/* Single summary row: badges + examples on the left, total · €/d · range per cell */}
      <tr className="rec-row">
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

      {open && <ProviderRows category={category} durations={durations} colorByKey={colorByKey} />}
    </>
  )
}
