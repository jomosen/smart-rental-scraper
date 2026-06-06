import type { Category } from '../types'
import { eur, eurDay, fmtDateShort } from '../lib/format'

interface Props {
  category: Category
  durations: number[]
  colorByKey: Record<string, string>
}

/** Provider sub-rows of one category (the accordion body). Returns a list of <tr>. */
export default function ProviderRows({ category, durations, colorByKey }: Props) {
  return (
    <>
      {category.providers.map((pr, idx) => {
        const color = colorByKey[pr.provider_key] ?? '#97a0b0'
        const cellByDur = new Map(pr.cells.map((c) => [c.duration, c]))
        return (
          <tr className="prov-row" key={`${pr.provider_key}-${pr.models}-${idx}`}>
            <td className="cat">
              <span className="pdot" style={{ background: color }} />
              <span className="pname">{pr.provider_key.charAt(0).toUpperCase() + pr.provider_key.slice(1)}</span>
              <span className="pmodels">{pr.models || '—'}</span>
              {pr.inferred && (
                <span
                  className="inf-badge"
                  title={`Derivado de su zona de precios${pr.observation_date ? ` (representante: ${fmtDateShort(pr.observation_date)})` : ''}`}
                >
                  INFERIDO
                </span>
              )}
            </td>
            {durations.map((d) => {
              const c = cellByDur.get(d)
              if (!c || c.missing) {
                return <td className="pcell" key={d}>—</td>
              }
              if (c.anomaly) {
                return (
                  <td className="pcell" style={{ color: 'var(--bad)' }} key={d}>
                    <s>{eur(c.total)}</s>
                  </td>
                )
              }
              const style = c.is_base
                ? { background: `${color}14`, borderRight: `3px solid ${color}` }
                : undefined
              return (
                <td className="pcell" style={style} key={d}>
                  {eur(c.total)}
                  <span className="pd">{eurDay(c.per_day)}</span>
                </td>
              )
            })}
          </tr>
        )
      })}
    </>
  )
}
